"""Retired branch-specific Colab cell builder.

The notebook and local launcher now execute the same submission/ package.
Use tools/create_colab_notebook.py, then script.py; this command does not run or
modify any experiment. Its former source is preserved in the local cleanup record.
"""
import argparse


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    parser.error("Retired: use tools/create_colab_notebook.py and the single script.py entry.")


if __name__ == "__main__":
    main()
