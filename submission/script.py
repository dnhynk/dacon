"""DACON entry point for pipeline C. All runtime code lives in pps_c/."""

if __name__ == "__main__":
    import sys

    from pps_c.main import main

    sys.exit(main())
