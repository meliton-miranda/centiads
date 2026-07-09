#!/usr/bin/env python3
"""Ejecuta SQL contra el Postgres del cockpit usando pg8000 (Python puro).

  python3 scripts/pgexec.py db/schema.sql        # ejecuta un archivo .sql (varias sentencias)
  python3 scripts/pgexec.py -c "select 1"        # ejecuta una sentencia e imprime filas
  cat file.sql | python3 scripts/pgexec.py -      # desde stdin

Lee DATABASE_URL del entorno o del archivo .env del proyecto.
"""
import os
import sys
import urllib.parse
from pathlib import Path


def load_env():
    if os.environ.get("DATABASE_URL"):
        return
    envf = Path(__file__).resolve().parent.parent / ".env"
    if envf.exists():
        for line in envf.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def connect():
    from pg8000.native import Connection

    u = urllib.parse.urlparse(os.environ["DATABASE_URL"])
    return Connection(
        user=urllib.parse.unquote(u.username or ""),
        password=urllib.parse.unquote(u.password or ""),
        host=u.hostname,
        port=u.port or 5432,
        database=(u.path or "/").lstrip("/") or "postgres",
    )


def strip_comments(sql: str) -> str:
    out = []
    for line in sql.splitlines():
        i = line.find("--")
        if i >= 0:
            line = line[:i]
        out.append(line)
    return "\n".join(out)


def split_statements(sql: str):
    return [p.strip() for p in strip_comments(sql).split(";") if p.strip()]


def main():
    load_env()
    args = sys.argv[1:]
    con = connect()
    try:
        if args and args[0] == "-c":
            rows = con.run(args[1])
            for r in rows or []:
                print("\t".join("" if c is None else str(c) for c in r))
        else:
            sql = sys.stdin.read() if (not args or args[0] == "-") else Path(args[0]).read_text()
            stmts = split_statements(sql)
            for s in stmts:
                con.run(s)
            print(f"OK: {len(stmts)} sentencias ejecutadas")
    finally:
        con.close()


if __name__ == "__main__":
    main()
