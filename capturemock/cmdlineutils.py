
""" Utility functions for command line interfaces """
import optparse

def create_option_parser():
    usage = """usage: %prog [options] <program> <program_args> ...

CaptureMock command line program. Records and replays interaction defined by stuff in its rc file"""

    parser = optparse.OptionParser(usage)
    parser.add_option("-m", "--mode", type="int", default=0,
                      help="CaptureMock mode. 0=replay, 1=record, 2=replay if possible, else record", metavar="MODE")
    parser.add_option("-p", "--replay", 
                      help="replay traffic recorded in FILE.", metavar="FILE")
    parser.add_option("-f", "--replay-file-edits", 
                      help="restore edited files referred to in replayed file from DIR.", metavar="DIR")
    parser.add_option("-r", "--record", 
                      help="record traffic to FILE.", metavar="FILE")
    parser.add_option("-F", "--record-file-edits", 
                      help="store edited files under DIR.", metavar="DIR")
    parser.add_option("-R", "--rcfiles", help="Read configuration from given rc files, defaults to ~/.capturemock/config")
    return parser
