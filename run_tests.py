#!/usr/bin/env python3
"""
Batch Test Launcher for luks-companion.
Runs the complete test suite with colorized summary output and coverage metrics.
Works identically on Linux (Raspberry Pi) and Windows development machines.
"""
import sys
import os
import subprocess
import time


def print_banner():
    banner = r"""
======================================================================
     __   _  _ _  ______       ___ ___  __  __ ___   _   _  _ ___ ___  _  _ 
    |  | | | | | |/ ___|     / __/ _ \|  \/  | _ \ /_\ | \| |_ _/ _ \| \| |
    |  |_| |_| ' <\___ \ ___| (_| (_) | |\/| |  _// _ \| .` || | (_) | .` |
    |____|\___/|_|\_\____/____|\___\___/|_|  |_|_| /_/ \_\_|\_|___\___/|_|\_|
               TEST SUITE BATCH RUNNER & INTEGRATION VERIFIER
======================================================================
    """
    print(banner, flush=True)


def main():
    print_banner()

    project_root = os.path.dirname(os.path.abspath(__file__))
    os.chdir(project_root)

    # Ensure project root is in PYTHONPATH
    current_pythonpath = os.environ.get("PYTHONPATH", "")
    os.environ["PYTHONPATH"] = f"{project_root}{os.pathsep}{current_pythonpath}"

    # Check if pytest is available
    try:
        import pytest
    except ImportError:
        print("[!] ERRORE: 'pytest' non è installato in questo ambiente Python.", file=sys.stderr)
        print("    Installa con: pip install pytest pytest-cov", file=sys.stderr)
        sys.exit(1)

    # Build pytest command line arguments
    pytest_args = ["-v", "--tb=short", os.path.join(project_root, "tests")]

    # Forward any CLI flags passed to run_tests.py (e.g. -k, -x, -s)
    forward_args = [arg for arg in sys.argv[1:] if arg != "--coverage"]
    pytest_args.extend(forward_args)

    # Check coverage flag or optional pytest-cov support
    if "--coverage" in sys.argv or "--cov" in sys.argv:
        try:
            import pytest_cov
            pytest_args.extend(["--cov=luks_companion", "--cov-report=term-missing"])
        except ImportError:
            print("[*] Nota: pytest-cov non installato, esecuzione standard senza coverage report.")

    t0 = time.time()
    print(f"[*] Avvio esecuzione test in: {os.path.join(project_root, 'tests')}...\n")

    exit_code = pytest.main(pytest_args)
    duration = round(time.time() - t0, 2)

    print("\n" + "=" * 70)
    if exit_code == 0:
        print(f"  [✓] TUTTI I TEST SONO PASSATI CON SUCCESSO IN {duration}s!")
    else:
        print(f"  [!] ALCUNI TEST SONO FALLITI (Exit code: {exit_code}) in {duration}s")
    print("=" * 70)

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
