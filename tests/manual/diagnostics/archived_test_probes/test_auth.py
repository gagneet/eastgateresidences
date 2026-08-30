import os

import asyncio
import jwt

from config import JWT_SECRET, JWT_ALGORITHM
from database import db


async def main():
    print(f"JWT_SECRET: {JWT_SECRET}")
    user = await db.users.find_one({"email": "gagneet@silverfoxtechnologies.com.au"})
    if not user:
        print("User not found")
        return
    print(f"User found: {user['id']}")

    token = jwt.encode({
        "user_id": user["id"],
        "email": user["email"],
        "role": user["role"],
        "building_id": "13195",
        "exp": 1900000000  # far future
    }, JWT_SECRET, algorithm=JWT_ALGORITHM)
    print(f"Generated Token: {token}")

    try:
        decoded = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        print(f"Decoded successfully: {decoded}")
    except Exception as e:
        print(f"Decode failed: {e}")


if __name__ == "__main__":
    os.environ["MONGO_URL"] = "mongodb://localhost:27018"
    os.environ["DB_NAME"] = "strata_production"
    asyncio.run(main())
