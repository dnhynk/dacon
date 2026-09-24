"""The single DACON entry point. All runtime code lives in submission/."""

if __name__ == "__main__":
    import sys

    from submission.main import main

    sys.exit(main())
