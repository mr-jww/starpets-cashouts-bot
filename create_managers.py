"""
One-time script: create manager users in DB with real Telegram IDs.
Run: python create_managers.py
"""
import asyncio
import aiosqlite
from config import DB_PATH

MANAGERS = [
    # (telegram_id, username, manager_filter)
    (989599679,  "Mr_JWW",             "John"),
    (451702760,  "fury870",            "Alex"),
    (1510083998, "alexcasher",         "Rocky"),
    (7643558802, "maxstarpets",        "Max"),
    (7561507537, "smazakxd",           "smazakxd"),
    (8588449413, "Awhswirl",           "Swirl"),
    (1184374252, "orannigga",          "Maison"),
    (143504344,  "nirimri",            "Emilio"),
    (192789622,  "vandaag_n",          "Nick"),
    (511907447,  "d_osipenkova",       "Jennifer"),
    (834987668,  "egoroluhov",         "Marco"),
    (857758001,  "oxytocin00",         "Ketty"),
    (872296363,  "mitsukko",           "Rina"),
    (876571424,  "a_nasty_v",          "Stacy"),
    (991864841,  "Flow_zero",          "Antonio"),
    (1364119194, "Zeoxxxxx",           "Tony"),
    (5743871159, "StarpetsTom",        "Tom"),
    (5929678915, "TalonStarPets",      "Talon"),
    (6034998239, "Vaneessaq",          "Vanessa"),
    (6299050609, "montaak",            "Monty"),
    (6712024602, "Unfairbird",         "Unfairbird"),
    (6712537576, "vojtixx",            "Vojtěch"),
    (7549285492, "Seb_starpets",       "Seb"),
    (7903428381, "seekformoney",       "Annalice"),
    (1874029307, "sergo_sp_manager",   "Sergo"),
]


async def main():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        created = updated = skipped = 0

        for tg_id, username, mgr_name in MANAGERS:
            # Check by telegram_id
            async with db.execute(
                "SELECT id, manager_filter FROM users WHERE telegram_id = ?", (tg_id,)
            ) as cur:
                existing = await cur.fetchone()

            if existing:
                existing = dict(existing)
                if not existing["manager_filter"]:
                    await db.execute(
                        "UPDATE users SET manager_filter = ?, username = ? WHERE telegram_id = ?",
                        (mgr_name, username, tg_id)
                    )
                    await db.commit()
                    print(f"  ~ {mgr_name}: updated manager_filter")
                    updated += 1
                else:
                    print(f"  = {mgr_name}: already exists, filter={existing['manager_filter']}")
                    skipped += 1
                continue

            # Check if placeholder exists for this manager name
            async with db.execute(
                "SELECT id FROM users WHERE manager_filter = ? COLLATE NOCASE AND telegram_id < 0",
                (mgr_name,)
            ) as cur:
                placeholder = await cur.fetchone()

            if placeholder:
                # Upgrade placeholder to real account
                await db.execute(
                    "UPDATE users SET telegram_id = ?, username = ? WHERE id = ?",
                    (tg_id, username, placeholder["id"])
                )
                await db.commit()
                print(f"  ^ {mgr_name}: upgraded placeholder → real id={tg_id}")
                updated += 1
                continue

            # Create new
            await db.execute(
                """INSERT INTO users
                   (telegram_id, username, role, lang, manager_filter,
                    output_mode, default_fmt,
                    include_paid, warn_paid, include_pending, warn_pending)
                   VALUES (?, ?, 'manager', 'en', ?, 'block', 'oneline', 0, 1, 0, 1)
                   ON CONFLICT(telegram_id) DO NOTHING""",
                (tg_id, username, mgr_name)
            )
            await db.commit()
            print(f"  + {mgr_name}: created (id={tg_id})")
            created += 1

        print(f"\nDone. Created: {created}, updated: {updated}, skipped: {skipped}")


if __name__ == "__main__":
    asyncio.run(main())