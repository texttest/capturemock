#!/usr/local/bin/python

helpOptions = """
-b <bname> - run in batch mode, using batch session name <bname>. This will replace the interactive
             dialogue with an email report, which is sent to $USER if the session name <bname> is
             not recognised by the config file.

             There is also a possibility to define batch sessions in the config file. The following
             entries are understood:
             <bname>_timelimit,  if present, will run only tests up to that limit
             <bname>_recipients, if present, ensures that mail is sent to those addresses instead of $USER.
             If set to "none", it ensures that that batch session will ignore that application.
             <bname>_version, these entries form a list and ensure that only the versions listed are accepted.
             If the list is empty, all versions are allowed.
             <bname>_use_collection, if equal to "true", send the batch report to an intermediate file where it
             can be collected and amalgamated with others using the script batch.CollectFiles. This avoids too
             many emails being sent by batch mode if many independent things are tested.
"""

import os, performance, plugins, respond, sys, string, time, types, smtplib
from ndict import seqdict
from cPickle import Pickler

# Class to fake mail sending
class FakeSMTP:
    def connect(self, server):
        print "Connecting to fake SMTP server at", server
    def sendmail(self, fromAddr, toAddresses, contents):
        print "Sending mail from address", fromAddr
        raise smtplib.SMTPServerDisconnected, "Could not send mail to " + repr(toAddresses) + ": I'm only a fake server!"
    def quit(self):
        pass

class BatchFilter(plugins.Filter):
    def __init__(self, batchSession):
        self.batchSession = batchSession
        self.performanceFilter = None
    def acceptsTestCase(self, test):
        if self.performanceFilter == None:
            return 1
        else:
            return self.performanceFilter.acceptsTestCase(test)
    def acceptsApplication(self, app):
        badVersion = self.findUnacceptableVersion(app)
        if badVersion != None:
            print "Rejected application", app, "for", self.batchSession, "session, unregistered version '" + badVersion + "'"
            return 0
        
        self.setTimeLimit(app)
        return 1
    def setTimeLimit(self, app):
        timeLimit = app.getCompositeConfigValue("batch_timelimit", self.batchSession)
        if timeLimit:
            self.performanceFilter = performance.TimeFilter(timeLimit)
    def findUnacceptableVersion(self, app):
        allowedVersions = app.getCompositeConfigValue("batch_version", self.batchSession)
        for version in app.versions:
            if len(version) and not version in allowedVersions:
                return version
        return None

class BatchCategory(plugins.Filter):
    def __init__(self, state):
        self.name = state.category
        if state.categoryDescriptions.has_key(self.name):
            self.briefDescription, self.longDescription = state.categoryDescriptions[self.name]
        else:
            self.briefDescription, self.longDescription = self.name, self.name
        self.allTests = []
        self.testLines = {}
    def addTest(self, test):
        overall, postText = test.state.getTypeBreakdown()
        if postText == self.name.upper():
            # Don't double report here
            postText = ""
        elif len(postText) > 0:
            postText = " : " + postText
        self.testLines[test.getRelPath()] = test.getIndent() + "- " + repr(test) + postText + "\n"
        self.allTests.append(test)
    def acceptsTestCase(self, test):
        return self.testLines.has_key(test.getRelPath())
    def describeBrief(self, app):
        if len(self.allTests) > 0:
            valid, suite = app.createTestSuite([ self ])
            return "The following tests " + self.longDescription + " : \n" + \
                   self.getTestLines(suite) + "\n"
    def getTestLines(self, test):
        if test.classId() == "test-case":
            return self.testLines[test.getRelPath()]
        else:
            lines = test.getIndent() + "In " + repr(test) + ":\n"
            for subtest in test.testcases:
                lines += self.getTestLines(subtest)
            return lines
    def describeFull(self):
        fullDescriptionString = self.getFullDescription()
        if fullDescriptionString:
            return "\nDetailed information for the tests that " + self.longDescription + " follows...\n" + fullDescriptionString
        else:
            return ""
    def getFullDescription(self):
        fullText = ""
        for test in self.allTests:
            freeText = test.state.freeText
            if freeText:
                fullText += "--------------------------------------------------------" + "\n"
                fullText += "TEST " + repr(test.state) + " " + repr(test) + " (under " + test.getRelPath() + ")" + "\n"
                fullText += freeText
                if not freeText.endswith("\n"):
                    fullText += "\n"
        return fullText

allBatchResponders = []

# Works only on UNIX
class BatchResponder(respond.Responder):
    def __init__(self, sessionName):
        respond.Responder.__init__(self, 0)
        self.sessionName = sessionName
        self.categories = {}
        self.errorCategories = []
        self.failureCategories = []
        self.successCategories = []
        self.mainSuite = None
        allBatchResponders.append(self)
    def handleAll(self, test):
        category = test.state.category
        if not self.categories.has_key(category):
            batchCategory = BatchCategory(test.state)
            if not test.state.hasResults():
                self.errorCategories.append(batchCategory)
            elif test.state.hasSucceeded():
                self.successCategories.append(batchCategory)
            else:
                self.failureCategories.append(batchCategory)
            self.categories[category] = batchCategory
        self.categories[category].addTest(test)
    def useGraphicalComparison(self, comparison):
        return 0
    def setUpSuite(self, suite):
        if self.mainSuite == None:
            self.mainSuite = suite
    def failureCount(self):
        return self.totalTests(self.failCategories())
    def successCount(self):
        return self.totalTests(self.successCategories)
    def failCategories(self):
        return self.errorCategories + self.failureCategories
    def allCategories(self):
        return self.failCategories() + self.successCategories
    def testCount(self):
        return self.totalTests(self.allCategories())
    def totalTests(self, categoryList):
        count = 0
        for category in categoryList:
            count += len(category.allTests)
        return count
    def getFailuresBrief(self):
        contents = ""
        for category in self.failCategories():
            contents += category.describeBrief(self.mainSuite.app)
        return contents
    def getSuccessBrief(self):
        contents = ""
        for category in self.successCategories:
            contents += category.describeBrief(self.mainSuite.app)
        return contents
    def getDetails(self):
        contents = ""
        for category in self.allCategories():
            contents += category.describeFull()
        return contents
    def getCleanUpAction(self):
        return MailSender(self.sessionName)

sectionHeaders = [ "Summary of all Unsuccessful tests", "Details of all Unsuccessful tests", "Summary of all Successful tests" ]

class MailSender(plugins.Action):
    def __init__(self, sessionName):
        self.sessionName = sessionName
        self.diag = plugins.getDiagnostics("Mail Sender")
    def getResponders(self, app):
        appResponders = []
        for responder in allBatchResponders:
            if responder.mainSuite:
                self.diag.info("Responder for " + responder.mainSuite.app.name + " has " + str(responder.testCount()) + " tests.")
            else:
                self.diag.info("Responder with main suite " + str(responder.mainSuite))
            if responder.mainSuite and responder.mainSuite.app.name == app.name and responder.testCount() > 0:
                appResponders.append(responder)
        return appResponders
    def setUpApplication(self, app):
        appResponders = self.getResponders(app)
        if len(appResponders) == 0:
            self.diag.info("No responders for " + repr(app))
            return
        mailTitle = self.getMailTitle(app, appResponders)
        mailContents = self.createMailHeaderSection(mailTitle, app, appResponders)
        if len(appResponders) > 1:
            for resp in appResponders:
                mailContents += self.getMailTitle(app, [ resp ]) + "\n"
            mailContents += "\n"
        if not self.isAllSuccess(appResponders):
            mailContents += self.performForAll(app, appResponders, BatchResponder.getFailuresBrief, sectionHeaders[0])
            mailContents += self.performForAll(app, appResponders, BatchResponder.getDetails, sectionHeaders[1])
        if not self.isAllFailure(appResponders):
            mailContents += self.performForAll(app, appResponders, BatchResponder.getSuccessBrief, sectionHeaders[2])
        for responder in appResponders:
            allBatchResponders.remove(responder)
        self.sendOrStoreMail(app, mailContents)
    def performForAll(self, app, appResponders, method, headline):
        contents = headline + " follows...\n" + \
                   "---------------------------------------------------------------------------------" + "\n"
        for resp in appResponders:
            if len(appResponders) > 1:
                if headline.find("Details") != -1 and not resp is appResponders[0]:
                    contents += "---------------------------------------------------------------------------------" + "\n"
                contents += self.getMailTitle(app, [ resp ]) + "\n\n"
            contents += method(resp) + "\n"
        return contents
    def storeMail(self, app, mailContents):
        localFileName = "batchreport." + app.name + app.versionSuffix()
        collFile = os.path.join(app.writeDirectory, localFileName)
        if not self.useCollection(app):
            root, local = os.path.split(app.writeDirectory)
            collFile = self.findAvailable(os.path.join(root, localFileName))
        self.diag.info("Sending mail to", collFile)
        file = open(collFile, "w")
        file.write(mailContents)
        file.close()
    def getSmtp(self):
        # Mock out sending of mail...
        if os.environ.has_key("TEXTTEST_FAKE_SEND_MAIL"):
            return FakeSMTP()
        else:
            return smtplib.SMTP()
    def sendOrStoreMail(self, app, mailContents):
        sys.stdout.write("At " + time.strftime("%H:%M") + " creating batch report for application " + repr(app) + " ...")
        sys.stdout.flush()
        if self.useCollection(app):
            self.storeMail(app, mailContents)
            sys.stdout.write("file written")
        else:
            # Write the result in here...
            smtp = self.getSmtp()
            self.sendMail(smtp, app, mailContents)
            smtp.quit()
        sys.stdout.write("\n")
    def sendMail(self, smtp, app, mailContents):
        smtpServer = app.getConfigValue("smtp_server")
        fromAddress = app.getCompositeConfigValue("batch_sender", self.sessionName)
        toAddresses = plugins.commasplit(app.getCompositeConfigValue("batch_recipients", self.sessionName))
        try:
            smtp.connect(smtpServer)
        except smtplib.SMTPException:
            sys.stdout.write("FAILED : Could not connect to SMTP server\n" + \
                             str(sys.exc_type) + ": " + str(sys.exc_value))
            return self.storeMail(app, mailContents)
        try:
            smtp.sendmail(fromAddress, toAddresses, mailContents)
        except smtplib.SMTPException:
            sys.stdout.write("FAILED : Mail could not be sent\n" + \
                             str(sys.exc_type) + ": " + str(sys.exc_value))
            return self.storeMail(app, mailContents)
        sys.stdout.write("done.")
    def findAvailable(self, origFile):
        if not os.path.isfile(origFile):
            return origFile
        for i in range(20):
            attempt = origFile + str(i)
            if not os.path.isfile(attempt):
                return attempt
    
    def createMailHeaderSection(self, title, app, appResponders):
        toAddress = app.getCompositeConfigValue("batch_recipients", self.sessionName)
        # blank line needed to separate headers from body
        if self.useCollection(app):
            return toAddress + "\n" + \
                   self.getMachineTitle(app, appResponders) + "\n" + \
                   title + "\n\n" # blank line separating headers from body
        else:
            fromAddress = app.getCompositeConfigValue("batch_sender", self.sessionName)
            return "From: " + fromAddress + "\nTo: " + toAddress + "\n" + \
                   "Subject: " + title + "\n\n"
    def useCollection(self, app):
        return app.getCompositeConfigValue("batch_use_collection", self.sessionName) == "true"
    def getMailHeader(self, app, appResponders):
        title = time.strftime("%y%m%d") + " " + repr(app)
        versions = self.findCommonVersions(app, appResponders)
        return title + self.getVersionString(versions) + " : "
    def getCategoryNames(self, appResponders):
        names = []
        for resp in appResponders:
            for cat in resp.errorCategories:
                if not cat.name in names:
                    names.append(cat.name)
        for resp in appResponders:
            for cat in resp.failureCategories:
                if not cat.name in names:
                    names.append(cat.name)
        for resp in appResponders:
            for cat in resp.successCategories:
                if not cat.name in names:
                    names.append(cat.name)
        return names
    def isAllSuccess(self, appResponders):
        return self.getTotalString(appResponders, BatchResponder.failureCount) == "0"
    def isAllFailure(self, appResponders):
        return self.getTotalString(appResponders, BatchResponder.successCount) == "0"
    def getMailTitle(self, app, appResponders):
        title = self.getMailHeader(app, appResponders)
        title += self.getTotalString(appResponders, BatchResponder.testCount) + " tests"
        if self.isAllSuccess(appResponders):
            return title + ", all successful"
        title += " :"
        for categoryName in self.getCategoryNames(appResponders):
            totalInCategory = self.getCategoryCount(categoryName, appResponders)
            briefDesc = self.getBriefDescription(categoryName, appResponders) 
            title += self.briefText(totalInCategory, briefDesc)
        # Lose trailing comma
        return title[:-1]
    def getMachineTitle(self, app, appResponders):
        values = []
        for categoryName in self.getCategoryNames(appResponders):
            countStr = str(self.getCategoryCount(categoryName, appResponders))
            briefDesc = self.getBriefDescription(categoryName, appResponders)
            values.append(briefDesc + "=" + countStr)
        return string.join(values, ',')
    def getTotalString(self, appResponders, method):
        total = 0
        for resp in appResponders:
            total += method(resp)
        return str(total)
    def getCategoryCount(self, categoryName, appResponders):
        total = 0
        for resp in appResponders:
            if resp.categories.has_key(categoryName):
                total += len(resp.categories[categoryName].allTests)
        return total
    def getBriefDescription(self, categoryName, appResponders):
        for resp in appResponders:
            if resp.categories.has_key(categoryName):
                return resp.categories[categoryName].briefDescription
    def getVersionString(self, versions):
        if len(versions) > 0:
            return " " + string.join(versions, ".")
        else:
            return ""
    def briefText(self, count, description):
        if count == 0 or description == "succeeded":
            return ""
        else:
            return " " + str(count) + " " + description + ","
    def findCommonVersions(self, app, appResponders):
        if len(appResponders) == 0:
            return app.versions
        versions = appResponders[0].mainSuite.app.versions
        for resp in appResponders[1:]:
            for version in versions:
                if not version in resp.mainSuite.app.versions:
                    versions.remove(version)
        return versions
        
class CollectFiles(plugins.Action):
    def __init__(self, args=[""]):
        self.mailSender = MailSender("collection")
        self.diag = plugins.getDiagnostics("batch collect")
        self.userName = args[0]
        if self.userName:
            print "Collecting batch files created by user", self.userName + "..."
        else:
            print "Collecting batch files locally..."
    def scriptDoc(self):
        return "Collect and send all batch reports that have been written to intermediate files"
    def setUpApplication(self, app):
        fileBodies = []
        totalValues = seqdict()
        # Collection should not itself use collection
        app.addConfigEntry("collection", "false", "batch_use_collection")
        userName, rootDir = app.getPreviousWriteDirInfo(self.userName)
        dirlist = os.listdir(rootDir)
        dirlist.sort()
        for dir in dirlist:
            fullDir = os.path.join(rootDir, dir)
            if os.path.isdir(fullDir) and dir.startswith(app.name + app.versionSuffix()):
                fileBodies += self.parseDirectory(fullDir, app, totalValues)
        if len(fileBodies) == 0:
            return
        
        mailTitle = self.getTitle(app, totalValues)
        mailContents = self.mailSender.createMailHeaderSection(mailTitle, app, [])
        mailContents += self.getBody(fileBodies)
        self.mailSender.sendOrStoreMail(app, mailContents)
    def parseDirectory(self, fullDir, app, totalValues):
        prefix = "batchreport." + app.name + app.versionSuffix()
        # Don't collect to more collections!
        self.diag.info("Setting up application " + app.name + " looking for " + prefix) 
        filelist = os.listdir(fullDir)
        filelist.sort()
        fileBodies = []
        for filename in filelist:
            if filename.startswith(prefix):
                fullname = os.path.join(fullDir, filename)
                fileBody = self.parseFile(fullname, app, totalValues)
                fileBodies.append(fileBody)
        return fileBodies
    def parseFile(self, fullname, app, totalValues):
        localName = os.path.basename(fullname)
        print "Found file called", localName
        file = open(fullname)
        recipient = file.readline().strip()
        if recipient:
            app.addConfigEntry("collection", recipient, "batch_recipients")
        catValues = plugins.commasplit(file.readline().strip())
        try:
            for value in catValues:
                catName, count = value.split("=")
                if not totalValues.has_key(catName):
                    totalValues[catName] = 0
                totalValues[catName] += int(count)
        except ValueError:
            print "WARNING : found truncated or old format batch report (" + localName + ") - could not parse result correctly"
        fileBody = file.read()
        file.close()
        return fileBody
    def getTitle(self, app, totalValues):
        title = self.mailSender.getMailHeader(app, [])
        total = 0
        for value in totalValues.values():
            total += value
        title += str(total) + " tests ran"
        if len(totalValues.keys()) == 1:
            return title + ", all " + totalValues.keys()[0]
        title += " :"
        for catName, count in totalValues.items():
            title += self.mailSender.briefText(count, catName)
        # Lose trailing comma
        return title[:-1]
    def extractHeader(self, body):
        firstSep = body.find("\n") + 1
        header = body[0:firstSep]
        return header, body[firstSep:]
    def extractSection(self, sectionHeader, body):
        headerLoc = body.find(sectionHeader)
        if headerLoc == -1:
            return body.strip(), ""
        nextLine = body.find("\n", headerLoc) + 1
        if body[nextLine] == "-":
            nextLine = body.find("\n", nextLine) + 1
        section = body[0:headerLoc].strip()
        newBody = body[nextLine:].strip()
        return section, newBody
    def getBody(self, bodies):
        if len(bodies) == 1:
            return bodies[0]

        totalBody = ""
        parsedBodies = []
        for subBody in bodies:
            header, parsedSubBody = self.extractHeader(subBody)
            totalBody += header
            parsedBodies.append((header, parsedSubBody))
        totalBody += "\n"

        sectionMap = {}
        prevSectionHeader = ""
        for sectionHeader in sectionHeaders:
            parsedSections = []
            newParsedBodies = []
            for header, body in parsedBodies:
                section, newBody = self.extractSection(sectionHeader, body)
                if len(newBody) != 0:
                    newParsedBodies.append((header, newBody))
                if len(section) != 0:
                    parsedSections.append((header, section))

            totalBody += self.getSectionBody(prevSectionHeader, parsedSections)
            parsedBodies = newParsedBodies
            prevSectionHeader = sectionHeader
        totalBody += self.getSectionBody(prevSectionHeader, parsedBodies)
        return totalBody
    def getSectionBody(self, sectionHeader, parsedSections):
        if len(sectionHeader) == 0 or len(parsedSections) == 0:
            return ""
        sectionBody = sectionHeader + " follows...\n"
        detailSection = sectionHeader.find("Details") != -1
        if not detailSection or len(parsedSections) == 1: 
            sectionBody += "=================================================================================\n"
        for header, section in parsedSections:
            if len(parsedSections) > 1:
                if detailSection:
                    sectionBody += "=================================================================================\n"
                sectionBody += header + "\n"
            sectionBody += section + "\n\n"
        return sectionBody
