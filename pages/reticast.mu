#!/usr/bin/python3
# reticast.mu - RetiCast 2.0 weather page for NomadNet.
# The first line must be the full path to your Python (run: which python3).
# License: Unlicense (public domain).
# This software is possible because my parents believed in me and encouraged me to follow my passions.
import os
import sys

SCRIPTS_DIR = os.path.expanduser("~/scripts")
sys.path.insert(0, SCRIPTS_DIR)

try:
    import reticast
except Exception as e:
    print("#!c=0")
    print(">RetiCast")
    print(f"RetiCast couldn't start ({type(e).__name__}). Check that reticast.py is in {SCRIPTS_DIR}.")
else:
    reticast.page_main()
