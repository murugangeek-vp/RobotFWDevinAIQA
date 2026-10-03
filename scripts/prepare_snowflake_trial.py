"""Prepare trial-only setup SQL, or explicitly run read-only live verification."""

import argparse
import csv
import getpass
import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data" / "samples" / "customer.csv"
CONTRACT = ROOT / "config" / "contracts" / "customer_snowflake.yaml"
TRIAL_ENV = ROOT / "config" / "environments" / "trial_snowflake.yaml"


def configured_account() -> str:
    import yaml

    account = yaml.safe_load(TRIAL_ENV.read_text(encoding="utf-8"))["target"]["account"]
    if not isinstance(account, str) or not re.fullmatch(r"[A-Za-z0-9_]+-[A-Za-z0-9_]+", account):
        raise ValueError("Set an organization-account identifier in trial_snowflake.yaml")
    return account


def sql_literal(value: str) -> str:
    if "$$" in value:
        raise ValueError("Dollar-quoted delimiters are not supported in the trial fixture")
    return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"


def public_key_value(public_pem: bytes) -> str:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = serialization.load_pem_public_key(public_pem)
    if not isinstance(key, rsa.RSAPublicKey) or key.key_size < 2048:
        raise ValueError("Snowflake requires an RSA public key of at least 2048 bits")
    pem = key.public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return "".join(pem.decode("ascii").splitlines()[1:-1])


def create_key_pair(directory: Path, passphrase: str) -> bytes:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    if len(passphrase) < 16:
        raise ValueError("Use a private-key passphrase of at least 16 characters")
    directory = directory.expanduser().resolve()
    if directory == ROOT or ROOT in directory.parents:
        raise ValueError("Private keys must be stored outside the repository")
    private_path = directory / "recon_ro.pem"
    public_path = directory / "recon_ro.pub"
    if private_path.exists() or public_path.exists():
        raise FileExistsError("Key files already exist; reuse the public key with --public-key")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.BestAvailableEncryption(passphrase.encode("utf-8")),
    )
    public_pem = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    fd = os.open(private_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(private_pem)
    with public_path.open("xb") as stream:
        stream.write(public_pem)
    return public_pem


def build_setup_sql(account: str, public_pem: bytes | None = None) -> str:
    import yaml

    if not re.fullmatch(r"[A-Za-z0-9_]+-[A-Za-z0-9_]+", account):
        raise ValueError("Use the organization-account identifier, not a URL")
    contract = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))
    with SOURCE.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != contract["source"]["columns"]:
            raise ValueError("Sample CSV headers differ from the Snowflake contract")
        rows = list(reader)
    if len(rows) != contract["metadata"]["expected_row_count"]:
        raise ValueError("Sample CSV count differs from the Snowflake contract")
    if any(None in row or any(v is None for v in row.values()) for row in rows):
        raise ValueError("Sample CSV contains malformed rows")
    payload = sql_literal(json.dumps(rows, ensure_ascii=True))
    auth = ""
    if public_pem is not None:
        public_key = public_key_value(public_pem)
        auth = f"""
CREATE USER RECON_SF_RO
    TYPE = SERVICE
    DEFAULT_ROLE = RECON_RO_ROLE
    DEFAULT_WAREHOUSE = RECON_WH
    DEFAULT_NAMESPACE = 'RECON.PUBLIC'
    DEFAULT_SECONDARY_ROLES = ()
    RSA_PUBLIC_KEY = '{public_key}';
GRANT ROLE RECON_RO_ROLE TO USER RECON_SF_RO;
"""
    return f"""USE ROLE ACCOUNTADMIN;

EXECUTE IMMEDIATE $$
DECLARE
    wrong_account EXCEPTION (-20001, 'Wrong account: use only the intended trial account');
    loaded_count NUMBER;
BEGIN
    IF (UPPER(CURRENT_ORGANIZATION_NAME() || '-' || CURRENT_ACCOUNT_NAME()) <> '{account.upper()}') THEN
        RAISE wrong_account;
    END IF;
    CREATE DATABASE RECON;
    CREATE WAREHOUSE RECON_WH
        WAREHOUSE_SIZE = 'XSMALL'
        AUTO_SUSPEND = 60
        AUTO_RESUME = TRUE
        INITIALLY_SUSPENDED = TRUE;
    CREATE ROLE RECON_RO_ROLE;
    CREATE TABLE RECON.PUBLIC.CUSTOMER (
        CUSTOMER_ID NUMBER(38,0) NOT NULL,
        FULL_NAME VARCHAR(201) NOT NULL,
        EMAIL VARCHAR(320) NOT NULL,
        DOB DATE NOT NULL,
        COUNTRY VARCHAR(2) NOT NULL,
        BALANCE NUMBER(18,2) NOT NULL,
        STATUS VARCHAR(255) NOT NULL,
        CREATED_AT TIMESTAMP_NTZ NOT NULL,
        PRIMARY KEY (CUSTOMER_ID)
    );

    USE WAREHOUSE RECON_WH;
    INSERT INTO RECON.PUBLIC.CUSTOMER
        (CUSTOMER_ID, FULL_NAME, EMAIL, DOB, COUNTRY, BALANCE, STATUS, CREATED_AT)
    SELECT
        VALUE:customer_id::NUMBER(38,0),
        VALUE:first_name::VARCHAR || ' ' || VALUE:last_name::VARCHAR,
        LOWER(VALUE:email::VARCHAR),
        TO_DATE(VALUE:dob::VARCHAR, 'YYYY-MM-DD'),
        UPPER(VALUE:country::VARCHAR),
        ROUND(TO_DECIMAL(VALUE:balance::VARCHAR, 18, 4), 2),
        VALUE:status::VARCHAR,
        TO_TIMESTAMP_NTZ(VALUE:created_at::VARCHAR, 'YYYY-MM-DD HH24:MI:SS')
    FROM TABLE(FLATTEN(INPUT => PARSE_JSON({payload})));

    GRANT USAGE ON DATABASE RECON TO ROLE RECON_RO_ROLE;
    GRANT USAGE ON SCHEMA RECON.PUBLIC TO ROLE RECON_RO_ROLE;
    GRANT USAGE ON WAREHOUSE RECON_WH TO ROLE RECON_RO_ROLE;
    GRANT SELECT ON TABLE RECON.PUBLIC.CUSTOMER TO ROLE RECON_RO_ROLE;
    {auth}
    SELECT COUNT(*) INTO :loaded_count FROM RECON.PUBLIC.CUSTOMER;
    RETURN 'Trial fixture rows: ' || loaded_count;
END;
$$;

SHOW GRANTS TO ROLE RECON_RO_ROLE;
SELECT COUNT(*) AS LOADED_ROWS FROM RECON.PUBLIC.CUSTOMER;
"""


def mcp_endpoint_url(account: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_]+-[A-Za-z0-9_]+", account):
        raise ValueError("Use the organization-account identifier, not a URL")
    host = account.lower().replace("_", "-")
    return (
        f"https://{host}.snowflakecomputing.com/api/v2/databases/RECON/"
        "schemas/PUBLIC/mcp-servers/RECON_MCP_RO"
    )


def build_mcp_sql(account: str) -> str:
    import yaml

    endpoint = mcp_endpoint_url(account)
    contract = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))
    expected_count = contract["metadata"]["expected_row_count"]
    return f"""USE ROLE ACCOUNTADMIN;

EXECUTE IMMEDIATE $$
DECLARE
    wrong_account EXCEPTION (-20001, 'Wrong account: use only the intended trial account');
    fixture_mismatch EXCEPTION (-20002, 'Trial fixture is absent or has an unexpected row count');
    fixture_count NUMBER;
BEGIN
    IF (UPPER(CURRENT_ORGANIZATION_NAME() || '-' || CURRENT_ACCOUNT_NAME()) <> '{account.upper()}') THEN
        RAISE wrong_account;
    END IF;
    SELECT COUNT(*) INTO :fixture_count FROM RECON.PUBLIC.CUSTOMER;
    IF (fixture_count <> {expected_count}) THEN
        RAISE fixture_mismatch;
    END IF;
END;
$$;

ALTER SCHEMA RECON.PUBLIC
    SET OAUTH_SCOPES_SUPPORTED = 'session:role:RECON_RO_ROLE';

CREATE MCP SERVER RECON.PUBLIC.RECON_MCP_RO
  FROM SPECIFICATION $$
tools:
  - title: "Read-only SQL verification"
    name: "read_only_sql"
    type: "SYSTEM_EXECUTE_SQL"
    description: "Execute bounded SELECT-only checks for independent reconciliation verification."
    config:
      read_only: true
      query_timeout: 60
      warehouse: "RECON_WH"
$$;

GRANT USAGE ON MCP SERVER RECON.PUBLIC.RECON_MCP_RO TO ROLE RECON_RO_ROLE;

CREATE SECURITY INTEGRATION RECON_MCP_OAUTH
    TYPE = OAUTH
    OAUTH_CLIENT = CUSTOM
    ENABLED = TRUE
    OAUTH_CLIENT_TYPE = 'CONFIDENTIAL'
    OAUTH_REDIRECT_URI = 'http://localhost:8765/callback'
    OAUTH_ALLOW_NON_TLS_REDIRECT_URI = TRUE
    OAUTH_ENFORCE_PKCE = TRUE
    OAUTH_ISSUE_REFRESH_TOKENS = TRUE
    OAUTH_REFRESH_TOKEN_VALIDITY = 86400
    OAUTH_USE_SECONDARY_ROLES = NONE
    ALLOWED_ROLES_LIST = ('RECON_RO_ROLE')
    COMMENT = 'Trial-only MCP OAuth client for read-only reconciliation checks';

EXECUTE IMMEDIATE $$
DECLARE
    quoted_login VARCHAR;
BEGIN
    quoted_login := '"' || REPLACE(CURRENT_USER(), '"', '""') || '"';
    EXECUTE IMMEDIATE 'GRANT ROLE RECON_RO_ROLE TO USER ' || quoted_login;
    EXECUTE IMMEDIATE 'ALTER USER ' || quoted_login ||
        ' SET DEFAULT_ROLE = ''RECON_RO_ROLE'' DEFAULT_WAREHOUSE = ''RECON_WH''';
END;
$$;

SHOW MCP SERVERS IN SCHEMA RECON.PUBLIC;
DESCRIBE MCP SERVER RECON.PUBLIC.RECON_MCP_RO;
SHOW GRANTS TO ROLE RECON_RO_ROLE;
SELECT SYSTEM$SHOW_OAUTH_CLIENT_SECRETS('RECON_MCP_OAUTH');
SELECT '{endpoint}' AS MCP_SERVER_URL;
"""


def verify_trial(account: str, key_dir: Path) -> int:
    import robot

    if account.upper() != configured_account().upper():
        raise ValueError("Verification account must match trial_snowflake.yaml")
    private_path = key_dir.expanduser().resolve() / "recon_ro.pem"
    if not private_path.is_file():
        raise FileNotFoundError("Create the encrypted trial key before running live verification")
    settings = {
        "SNOWFLAKE_ACCOUNT": account,
        "SNOWFLAKE_WAREHOUSE": "RECON_WH",
        "SNOWFLAKE_DATABASE": "RECON",
        "SNOWFLAKE_ROLE": "RECON_RO_ROLE",
        "RECON_RO_USER": "RECON_SF_RO",
        "SNOWFLAKE_PRIVATE_KEY_FILE": str(private_path),
        "SNOWFLAKE_PRIVATE_KEY_PASSWORD": getpass.getpass("Private-key passphrase: "),
        "SNOWFLAKE_LIVE_TESTS": "true",
    }
    previous = {key: os.environ.get(key) for key in settings}
    previous_directory = Path.cwd()
    try:
        os.environ.update(settings)
        os.chdir(ROOT)
        return robot.run(
            str(ROOT / "tests/snowflake/01_snowflake_target.robot"),
            variable=[f"SF_ENV:{TRIAL_ENV}"],
            outputdir=str(ROOT / "results_sf_live"),
        )
    finally:
        os.chdir(previous_directory)
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account", default=configured_account())
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--create-key", action="store_true")
    group.add_argument("--public-key", type=Path)
    group.add_argument("--verify", action="store_true")
    group.add_argument("--mcp-sql", action="store_true")
    parser.add_argument("--key-dir", type=Path, default=Path.home() / ".recon-snowflake-trial")
    parser.add_argument("--output", type=Path, default=ROOT / "results_sf" / "trial_setup.sql")
    parser.add_argument("--mcp-output", type=Path, default=ROOT / "results_sf" / "mcp_setup.sql")
    args = parser.parse_args()
    if args.mcp_sql:
        if args.mcp_output.exists():
            raise FileExistsError("Output already exists; choose a new --mcp-output path")
        args.mcp_output.parent.mkdir(parents=True, exist_ok=True)
        with args.mcp_output.open("x", encoding="utf-8") as stream:
            stream.write(build_mcp_sql(args.account))
        print(f"MCP SQL generated (not executed): {args.mcp_output}")
        print(f"MCP server URL: {mcp_endpoint_url(args.account)}")
        return
    build_setup_sql(args.account)
    if args.verify:
        raise SystemExit(verify_trial(args.account, args.key_dir))
    if args.output.exists():
        raise FileExistsError("Output already exists; choose a new --output path")
    public_pem = None
    if args.create_key:
        passphrase = getpass.getpass("New private-key passphrase (16+ characters): ")
        if passphrase != getpass.getpass("Confirm passphrase: "):
            raise ValueError("Passphrases do not match")
        public_pem = create_key_pair(args.key_dir, passphrase)
        print(f"Encrypted key saved outside the repository: {args.key_dir / 'recon_ro.pem'}")
    elif args.public_key:
        public_pem = args.public_key.read_bytes()
    output = build_setup_sql(args.account, public_pem)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        stream.write(output)
    print(f"SQL generated (not executed): {args.output}")
    if public_pem is None:
        print(
            "PREVIEW ONLY: no service user/key registration included. Use --create-key for setup."
        )


if __name__ == "__main__":
    main()
