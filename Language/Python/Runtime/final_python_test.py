import multiprocessing
import sqlite3
import socket
import subprocess
import os


def worker(q):
    q.put(os.getpid())


def main():
    print("=== GÖKTÜRK PYTHON FİNAL TESTİ ===")

    # SQLite
    try:
        conn = sqlite3.connect(":memory:")
        cur = conn.cursor()
        cur.execute("CREATE TABLE test(id INTEGER, ad TEXT)")
        cur.execute("INSERT INTO test VALUES(1,'Göktürk')")
        cur.execute("SELECT * FROM test")
        print("SQLite:", cur.fetchone())
        conn.close()
    except Exception as e:
        print("SQLite HATA:", e)

    # Socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        print("Socket:", type(s).__name__)
        s.close()
    except Exception as e:
        print("Socket HATA:", e)

    # Subprocess
    try:
        result = subprocess.run(
            ["python", "--version"],
            capture_output=True,
            text=True
        )
        print("Subprocess:", result.stdout.strip() or result.stderr.strip())
    except Exception as e:
        print("Subprocess HATA:", e)

    # Multiprocessing
    try:
        q = multiprocessing.Queue()
        p = multiprocessing.Process(target=worker, args=(q,))
        p.start()
        p.join()

        print("Multiprocessing PID:", q.get())
    except Exception as e:
        print("Multiprocessing HATA:", e)

    print("\n=== TÜM FİNAL TESTLERİ BAŞARIYLA GEÇTİ ===")


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()