
import sys

def trySetupCoverage(): # pragma: no cover - can hardly measure coverage here :)
    # This check shouldn't really be there.
    # It's a workaround because coverage lives in an egg, and if it isn't installed in the default location
    # it won't have been set up yet here. Which means it needs to be linked in directly, which means
    # that e.g. Python 2.4 dumps core if it tries to execute this code.
    if sys.version_info[:2] >= (2, 6):
        try:
            import coverage
            coverage.process_startup() # doesn't do anything unless COVERAGE_PROCESS_START is set
        except Exception: 
            pass


def trySetupTraffic():
    try:
        # partial traffic interception?
        import traffic_customize
    except ImportError:
        pass


def restoreOriginal():
    # Need to load the "real" sitecustomize now
    import os, sys, imp
    from copy import copy
    myDir = os.path.dirname(__file__)
    pathToSearch = copy(sys.path)
    pathToSearch.remove(myDir)
    modInfo = imp.find_module("sitecustomize", pathToSearch)
    imp.load_module("sitecustomize", *modInfo)

trySetupCoverage() # pragma: no cover - coverage not set up yet
trySetupTraffic() # pragma: no cover - coverage not set up yet
restoreOriginal() # pragma: no cover - coverage not set up yet