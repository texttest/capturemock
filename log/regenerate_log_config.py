#!/usr/bin/env python

import logconfiggen, os
    
if __name__ == "__main__":
    gen = logconfiggen.PythonLoggingGenerator("logging.conf", prefix="%(LOCAL_DIR)s/", postfix="diag")
    enabledLoggerNames = []
    
    installationRoot = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    coreLib = os.path.join(installationRoot, "capturemock")
    coreLoggers = logconfiggen.findLoggerNamesUnder(coreLib)
        
    gen.generate(enabledLoggerNames, coreLoggers, debugLevelLoggers=coreLoggers)
    
