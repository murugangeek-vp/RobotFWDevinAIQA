import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from scripts.prepare_snowflake_trial import (
    ROOT,
    build_mcp_sql,
    build_setup_sql,
    configured_account,
    create_key_pair,
    mcp_endpoint_url,
    public_key_value,
    sql_literal,
    verify_trial,
)


class TrialSetupTests(unittest.TestCase):
    def test_account_must_be_identifier_not_url_or_sql(self):
        for account in ("", "https://account", "org-account'; DROP", "org.account"):
            with self.subTest(account=account), self.assertRaises(ValueError):
                build_setup_sql(account)

    def test_sql_guards_account_and_existing_database(self):
        sql = build_setup_sql("EXAMPLE-TRIAL")
        self.assertIn("<> 'EXAMPLE-TRIAL'", sql)
        self.assertLess(sql.index("RAISE wrong_account"), sql.index("CREATE DATABASE"))
        self.assertIn("CREATE DATABASE RECON;", sql)
        for forbidden in ("DROP ", "TRUNCATE ", "DELETE ", "CREATE OR REPLACE", "IF NOT EXISTS"):
            self.assertNotIn(forbidden, sql.upper())

    def test_cost_controls_and_narrow_grants(self):
        sql = build_setup_sql("EXAMPLE-TRIAL")
        for expected in (
            "WAREHOUSE_SIZE = 'XSMALL'",
            "AUTO_SUSPEND = 60",
            "INITIALLY_SUSPENDED = TRUE",
            "GRANT SELECT ON TABLE RECON.PUBLIC.CUSTOMER TO ROLE RECON_RO_ROLE",
        ):
            self.assertIn(expected, sql)
        self.assertNotIn("GRANT ALL", sql)
        self.assertNotIn("GRANT ROLE ACCOUNTADMIN", sql)
        self.assertNotIn("GRANT SELECT ON ALL", sql)

    def test_preview_does_not_create_unauthenticated_user(self):
        self.assertNotIn("CREATE USER", build_setup_sql("EXAMPLE-TRIAL"))

    def test_mcp_sql_is_scoped_to_trial_and_read_only(self):
        sql = build_mcp_sql("EXAMPLE-TRIAL")
        self.assertIn("<> 'EXAMPLE-TRIAL'", sql)
        self.assertIn("CREATE MCP SERVER RECON.PUBLIC.RECON_MCP_RO", sql)
        self.assertIn("read_only: true", sql)
        self.assertIn("query_timeout: 60", sql)
        self.assertIn('warehouse: "RECON_WH"', sql)
        self.assertIn("OAUTH_USE_SECONDARY_ROLES = NONE", sql)
        self.assertIn("ALLOWED_ROLES_LIST = ('RECON_RO_ROLE')", sql)
        self.assertIn("OAUTH_ENFORCE_PKCE = TRUE", sql)
        self.assertIn("http://localhost:8765/callback", sql)
        self.assertIn("GRANT USAGE ON MCP SERVER RECON.PUBLIC.RECON_MCP_RO", sql)
        self.assertIn("GRANT ROLE RECON_RO_ROLE TO USER", sql)
        for forbidden in (
            "DROP ",
            "TRUNCATE ",
            "DELETE ",
            "CREATE OR REPLACE",
            "IF NOT EXISTS",
            "GRANT ALL",
            "ACCOUNTADMIN')",
        ):
            self.assertNotIn(forbidden, sql.upper())

    def test_mcp_endpoint_uses_snowflake_host_and_fixed_read_only_object(self):
        self.assertEqual(
            mcp_endpoint_url("EXAMPLE-TRIAL"),
            "https://example-trial.snowflakecomputing.com/api/v2/databases/RECON/"
            "schemas/PUBLIC/mcp-servers/RECON_MCP_RO",
        )
        for account in ("", "https://account", "org.account", "account'; DROP"):
            with self.subTest(account=account), self.assertRaises(ValueError):
                mcp_endpoint_url(account)

    def test_seed_uses_raw_source_and_independent_sql_transforms(self):
        import yaml

        sql = build_setup_sql("EXAMPLE-TRIAL")
        payload = sql.split("PARSE_JSON('", 1)[1].split("'))", 1)[0]
        rows = json.loads(payload.replace("''", "'").replace("\\\\", "\\"))
        contract = yaml.safe_load((ROOT / "config/contracts/customer_snowflake.yaml").read_text())
        self.assertEqual(len(rows), contract["metadata"]["expected_row_count"])
        self.assertIn("first_name", rows[0])
        self.assertNotIn("full_name", rows[0])
        self.assertIn("LOWER(VALUE:email", sql)
        self.assertIn("UPPER(VALUE:country", sql)
        self.assertIn("ROUND(TO_DECIMAL", sql)

    def test_literal_escaping(self):
        self.assertEqual(sql_literal("O'Brien\\path"), "'O''Brien\\\\path'")
        with self.assertRaises(ValueError):
            sql_literal("break $$ block")

    def test_encrypted_key_and_public_registration(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            passphrase = "temporary test passphrase only"
            public_pem = create_key_pair(path, passphrase)
            encrypted = (path / "recon_ro.pem").read_bytes()
            with self.assertRaises(TypeError):
                serialization.load_pem_private_key(encrypted, password=None)
            with self.assertRaises(ValueError):
                serialization.load_pem_private_key(encrypted, password=b"incorrect-test-passphrase")
            private_key = serialization.load_pem_private_key(encrypted, passphrase.encode())
            self.assertEqual(private_key.key_size, 3072)
            public_key = serialization.load_pem_public_key(public_pem)
            self.assertEqual(private_key.public_key().public_numbers(), public_key.public_numbers())
            sql = build_setup_sql("EXAMPLE-TRIAL", public_pem)
            self.assertIn("TYPE = SERVICE", sql)
            self.assertIn("DEFAULT_SECONDARY_ROLES = ()", sql)
            self.assertIn("RSA_PUBLIC_KEY = '", sql)
            self.assertNotIn(passphrase, sql)
            self.assertNotIn("PRIVATE KEY", sql)
            with self.assertRaises(FileExistsError):
                create_key_pair(path, passphrase)
            self.assertEqual((path / "recon_ro.pem").read_bytes(), encrypted)

    def test_weak_passphrase_and_repo_key_location_rejected(self):
        with self.assertRaises(ValueError):
            create_key_pair(ROOT, "short")
        with self.assertRaises(ValueError):
            create_key_pair(ROOT / "keys", "temporary test passphrase only")

    def test_invalid_public_key_rejected(self):
        with self.assertRaises(ValueError):
            public_key_value(b"not a public key")
        key = rsa.generate_private_key(public_exponent=65537, key_size=1024)
        pem = key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        with self.assertRaises(ValueError):
            public_key_value(pem)

    def test_account_defaults_to_saved_trial_configuration(self):
        import yaml

        config = yaml.safe_load((ROOT / "config/environments/trial_snowflake.yaml").read_text())
        self.assertEqual(configured_account(), config["target"]["account"])
        self.assertNotIn("${", configured_account())

    def test_verify_rejects_different_account(self):
        with self.assertRaisesRegex(ValueError, "must match"):
            verify_trial("OTHER-ACCOUNT", ROOT)

    def test_verify_requires_key_before_prompt(self):
        with tempfile.TemporaryDirectory() as directory, patch("getpass.getpass") as prompt:
            with self.assertRaises(FileNotFoundError):
                verify_trial(configured_account(), Path(directory))
            prompt.assert_not_called()

    def test_verify_uses_trial_profile_and_restores_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "recon_ro.pem").touch()
            with (
                patch.dict(os.environ, {"RECON_RO_USER": "original"}, clear=True),
                patch("getpass.getpass", return_value="temporary test passphrase only"),
                patch("robot.run") as run,
            ):

                def check_call(*args, **kwargs):
                    self.assertEqual(os.environ["RECON_RO_USER"], "RECON_SF_RO")
                    self.assertEqual(os.environ["SNOWFLAKE_ACCOUNT"], configured_account())
                    self.assertEqual(os.environ["SNOWFLAKE_LIVE_TESTS"], "true")
                    self.assertIn("trial_snowflake.yaml", kwargs["variable"][0])
                    self.assertNotIn("temporary test passphrase", str(kwargs))
                    return 0

                run.side_effect = check_call
                self.assertEqual(verify_trial(configured_account(), path), 0)
                self.assertEqual(os.environ["RECON_RO_USER"], "original")
                self.assertNotIn("SNOWFLAKE_PRIVATE_KEY_PASSWORD", os.environ)

    def test_generator_never_calls_connector(self):
        with patch("snowflake.connector.connect") as connect:
            build_setup_sql("EXAMPLE-TRIAL")
        connect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
