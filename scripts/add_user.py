#!/usr/bin/env python3
"""Crea o reinicia un usuario de Centiads con una contraseña aleatoria (se muestra una sola vez).

  DATABASE_URL=... python3 scripts/add_user.py marionava@netus.mx [meliton@netus.mx ...]
  python3 scripts/add_user.py --desactivar correo@netus.mx
"""
import secrets
import sys

import server
import webapp


def main():
    args = sys.argv[1:]
    if not args:
        raise SystemExit(__doc__)
    server.load_env()
    con = server.connect()
    try:
        if args[0] == "--desactivar":
            for e in args[1:]:
                con.run("update cockpit.app_users set active = false where email = :e", e=e.lower())
                print("desactivado:", e.lower())
            return
        for e in args:
            e = e.strip().lower()
            pw = secrets.token_urlsafe(12)
            con.run("""insert into cockpit.app_users(email, pass_hash) values (:e, :h)
                       on conflict (email) do update set pass_hash = excluded.pass_hash, active = true""",
                    e=e, h=webapp.hash_password(pw))
            print(f"{e}\t{pw}")
    finally:
        con.close()


if __name__ == "__main__":
    main()
