#!/usr/local/bin/python


import os, filecmp, plugins, time, stat
from ndict import seqdict
from shutil import copyfile

class FileComparison:
    def __init__(self, test, stem, standardFile, tmpFile, testInProgress = 0, observers={}):
        self.stdFile = standardFile
        self.stdCmpFile = self.stdFile
        self.tmpFile = tmpFile
        self.tmpCmpFile = tmpFile
        self.stem = stem
        self.differenceCache = False 
        self.diag = plugins.getDiagnostics("FileComparison")
        filterFileBase = test.makeTmpFileName(stem + "." + test.app.name, forFramework=1)
        origCmp = filterFileBase + "origcmp"
        if os.path.isfile(origCmp):
            self.stdCmpFile = origCmp
        tmpCmpFileName = filterFileBase + "cmp"
        if testInProgress:
            tmpCmpFileName = filterFileBase + "partcmp"
        if os.path.isfile(tmpCmpFileName):
            self.tmpCmpFile = tmpCmpFileName
        self.diag.info("File comparison std: " + repr(self.stdFile) + " tmp: " + repr(self.tmpFile))
        self.severity = test.getCompositeConfigValue("failure_severity", self.stem)
        self.displayPriority = test.getCompositeConfigValue("failure_display_priority", self.stem)
        maxLength = test.getConfigValue("lines_of_text_difference")
        maxWidth = test.getConfigValue("max_width_text_difference")
        self.previewGenerator = plugins.PreviewGenerator(maxWidth, maxLength)
        self.textDiffTool = test.getConfigValue("text_diff_program")
        self.textDiffToolMaxSize = plugins.parseBytes(test.getConfigValue("text_diff_program_max_file_size"))
        # subclasses may override if they don't want to store in this way
        self.cacheDifferences()
    def __repr__(self):
        return self.stem
    def ensureCompatible(self):
        if not hasattr(self, "differenceCache"):
            self.differenceCache = self.differenceId
        self.diag = plugins.getDiagnostics("FileComparison")
    def modifiedDates(self):
        files = [ self.stdFile, self.tmpFile, self.stdCmpFile, self.tmpCmpFile ]
        return " : ".join(map(self.modifiedDate, files))
    def modifiedDate(self, file):
        if not file:
            return "---"
        modTime = plugins.modifiedTime(file)
        if modTime:
            return time.strftime("%d%b%H:%M:%S", time.localtime(modTime))
        else:
            return "---"
    def needsRecalculation(self):
        if not self.stdFile or not self.tmpFile:
            self.diag.info("No comparison, no recalculation")
            return False

        # A test that has been saved doesn't need recalculating
        if self.tmpFile == self.stdFile:
            self.diag.info("Saved file, no recalculation")
            return False

        stdModTime = plugins.modifiedTime(self.stdFile)
        tmpModTime = plugins.modifiedTime(self.tmpFile)
        if stdModTime is not None and tmpModTime is not None and stdModTime >= tmpModTime:
            self.diag.info("Standard result newer than generated result at " + self.stdFile)
            return True

        if self.stdFile == self.stdCmpFile: # no filters
            return False
        
        stdCmpModTime = plugins.modifiedTime(self.stdCmpFile)
        tmpCmpModTime = plugins.modifiedTime(self.tmpCmpFile)
        if tmpModTime > tmpCmpModTime:
            self.diag.info("Filter for tmp file out of date")
            return True

        self.diag.info("Comparing timestamps for standard files")
        return stdCmpModTime is not None and stdModTime is not None and stdModTime >= stdCmpModTime
    def getType(self):
        return "failure"
    def getDisplayFileName(self):
        if self.newResult():
            return self.tmpFile
        else:
            return self.stdFile
    def getDetails(self):
        # Nothing to report above what is already known
        return ""
    def newResult(self):
        return not self.stdFile and self.tmpFile
    def missingResult(self):
        return self.stdFile and not self.tmpFile
    def isDefunct(self):
        return not self.stdFile and not self.tmpFile
    def hasSucceeded(self):
        return self.stdFile and self.tmpFile and not self.hasDifferences()
    def hasDifferences(self):
        return self.differenceCache
    def getStdFile(self, filtered):
        if filtered:
            return self.stdCmpFile
        else:
            return self.stdFile
    def getTmpFile(self, filtered):
        if filtered:
            return self.tmpCmpFile
        else:
            return self.tmpFile
    def existingFile(self, filtered):
        if self.missingResult():
            return self.getStdFile(filtered)
        else:
            return self.getTmpFile(filtered)
    def cacheDifferences(self):
        if self.stdCmpFile and self.tmpCmpFile:
            self.differenceCache = not filecmp.cmp(self.stdCmpFile, self.tmpCmpFile, 0)
    def getSummary(self, includeNumbers=True):
        if self.newResult():
            return self.stem + " new"
        elif self.missingResult():
            return self.stem + " missing"
        else:
            return self.getDifferencesSummary(includeNumbers)
    def getDifferencesSummary(self, includeNumbers=True):
        return self.stem + " different"
    def getFreeText(self):
        return self.getFreeTextTitle() + "\n" + self.getFreeTextBody()
    def getFreeTextTitle(self):
        if self.missingResult():
            titleText = "Missing result in"
        elif self.newResult():
            titleText = "New result in"
        else:
            titleText = "Differences in"
        titleText += " " + repr(self)
        return "------------------ " + titleText + " --------------------"
    def getFreeTextBody(self):
        if self.newResult():
            return self.previewGenerator.getPreview(open(self.tmpCmpFile))
        elif self.missingResult():
            return self.previewGenerator.getPreview(open(self.stdCmpFile))

        if plugins.canExecute(self.textDiffTool):
            stdFileSize = os.stat(self.stdCmpFile)[stat.ST_SIZE]
            tmpFileSize = os.stat(self.tmpCmpFile)[stat.ST_SIZE]
            if self.textDiffToolMaxSize >= 0 and (stdFileSize > self.textDiffToolMaxSize or tmpFileSize > self.textDiffToolMaxSize):
                message = "Warning: The files were too large to compare - " + str(stdFileSize) + " and " + \
                          str(tmpFileSize) + " bytes, compared to the limit of " + str(self.textDiffToolMaxSize) + \
                          " bytes. Double-click on the file to see the difference, or adjust text_diff_program_max_file_size" + \
                          " and re-run to see the difference in this text view.\n"
                return self.previewGenerator.getWrappedLine(message)
            
            cmdLine = self.textDiffTool + ' "' + self.stdCmpFile + '" "' + self.tmpCmpFile + '"'
            stdout = os.popen(cmdLine)
            return self.previewGenerator.getPreview(stdout)
        else:
            return "No difference report could be created: could not find textual difference tool '" + self.textDiffTool + "'"
    def updatePaths(self, oldAbsPath, newAbsPath):
        if self.stdFile:
            self.stdFile = self.stdFile.replace(oldAbsPath, newAbsPath)
            self.stdCmpFile = self.stdCmpFile.replace(oldAbsPath, newAbsPath)
        if self.tmpFile:
            self.tmpCmpFile = self.tmpCmpFile.replace(oldAbsPath, newAbsPath)
            self.tmpFile = self.tmpFile.replace(oldAbsPath, newAbsPath)
    def versionise(self, fileName, versionString):
        if len(versionString):
            return fileName + "." + versionString
        else:
            return fileName
    def getStdRootVersionFile(self):
        # drop version identifiers
        dirname, local = os.path.split(self.stdFile)
        localRoot = ".".join(local.split(".")[:2])
        return os.path.join(dirname, localRoot)
    def overwrite(self, test, exact, versionString):
        self.diag.info("save file from " + self.tmpFile)
        stdRoot = self.getStdRootVersionFile()
        self.stdFile = self.versionise(stdRoot, versionString)
        if os.path.isfile(self.stdFile):
            os.remove(self.stdFile)

        self.saveTmpFile(exact)
    def saveNew(self, test, versionString, diags):
        self.stdFile = os.path.join(test.getDirectory(), self.versionise(self.stem + "." + test.app.name, versionString))
        self.saveTmpFile()
    def saveTmpFile(self, exact=True):
        self.diag.info("Saving tmp file to " + self.stdFile)
        plugins.ensureDirExistsForFile(self.stdFile)
        # Allow for subclasses to differentiate between a literal overwrite and a
        # more intelligent save, e.g. for performance. Default is the same for exact
        # and inexact save
        if exact:
            copyfile(self.tmpFile, self.stdFile)
        else:
            self.saveResults(self.stdFile)
        # Try to get everything to behave normally after a save...
        self.differenceCache = False
        self.tmpFile = self.stdFile
        self.tmpCmpFile = self.stdFile
    def saveMissing(self, versionString, autoGenText):
        stdRoot = self.getStdRootVersionFile()
        targetFile = self.versionise(stdRoot, versionString)
        if os.path.isfile(targetFile):
            os.remove(targetFile)

        self.stdFile = None
        self.stdCmpFile = None
        if stdRoot != targetFile and os.path.isfile(stdRoot):
            # Create a "versioned-missing" file
            newFile = open(targetFile, "w")
            newFile.write(autoGenText)
            newFile.close()
    def saveResults(self, destFile):
        copyfile(self.tmpFile, destFile)
        
