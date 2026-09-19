"""
Reset password for user 8142177207 to Sasi#123
"""
import sqlite3
from argon2 import PasswordHasher

ph = PasswordHasher()
desired_password = "Sasi#123"
new_hash = ph.hash(desired_password)

con = sqlite3.connect("data/faucet.db")
cur = con.cursor()

cur.execute("SELECT id, full_name, mobile_number, normalized_mobile FROM users WHERE mobile_number LIKE '%8142177207%';")
users = cur.fetchall()
print(f"Found {len(users)} user(s):")
for u in users:
    print(f"  ID: {u[0]}, Name: {u[1]}, Mobile: {u[2]}, Norm: {u[3]}")
    cur.execute("UPDATE users SET password_hash = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?;", (new_hash, u[0]))
    print(f"  -> Successfully set password for {u[1]} ({u[2]}) to '{desired_password}'")

con.commit()
con.close()
print("Password update complete!")
