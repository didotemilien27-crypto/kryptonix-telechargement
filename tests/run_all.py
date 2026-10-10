"""Lance toute la batterie de tests des modules Jarvis :  python tests/run_all.py"""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warnings; warnings.simplefilter("ignore")
suite = unittest.defaultTestLoader.discover(os.path.dirname(os.path.abspath(__file__)), pattern="test_extensions_*.py")
res = unittest.TextTestRunner(verbosity=1).run(suite)
sys.exit(0 if res.wasSuccessful() else 1)
