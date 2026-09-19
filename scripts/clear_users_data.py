"""
Safe script to remove all existing users data while preserving admin configuration.
"""
import sqlite3
import shutil
import time
from pathlib import Path

db_path = Path("data/faucet.db")
if not db_path.exists():
    print("Database data/faucet.db does not exist.")
    exit(1)

# 1. Create a backup
backup_path = Path(f"data/faucet_backup_{int(time.time())}.db")
shutil.copyfile(db_path, backup_path)
print(f"Safety backup created at: {backup_path}")

# 2. Clear user data
con = sqlite3.connect(str(db_path))
cur = con.cursor()

tables_to_clear = ["users", "user_wallets", "usat_payments", "claims"]
for table in tables_to_clear:
    cur.execute(f"DELETE FROM {table};")
    print(f"Cleared table: {table}")

cur.execute("DELETE FROM sqlite_sequence WHERE name IN ('users', 'user_wallets', 'usat_payments', 'claims');")
con.commit()
con.execute("VACUUM;")
con.close()

# 3. Verify counts
con = sqlite3.connect(str(db_path))
cur = con.cursor()
check_tables = ["users", "claims", "settings", "user_wallets", "receiving_wallets", "usat_payments"]
counts = {t: cur.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in check_tables}
print("Verified database counts:", counts)
con.close()
