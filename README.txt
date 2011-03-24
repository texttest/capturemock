CaptureMock:
        A tool capable of capturing and replaying mock information for the purposes of testing. Supports
        - Python modules and attributes
        - System calls via the command line, including any files they write
        - Synchronous plain-text messaging over a network

        See the documentation at http://www.texttest.org/index.php?page=capturemock

System requirements:

    At least Python 2.6

Other Open Source Software packaged with it/used by it:

    ordereddict.py  : sequential dictionaries. (Raymond Hettinger, v1.1)

Installation:

    Go to the "source" directory and run "python setup.py install".
    On Windows, this will probably install to C:\Pythonxx\Scripts, which will then need to be in your PATH if you want to run it from the command line.

Documentation:

    http://www.texttest.org/index.php?page=capturemock

Test suite:

    The complete test suite (which uses Texttest) is provided in this download under "tests". It has a wealth of little example programs contained in it. It should be possible to run it via texttest.py -d <path to tests>
    (if you install TextTest, of course)

Bugs/Support:
    
    Write to the mailing list at texttest-users@lists.sourceforge.net
    Report bugs in the Launchpad bugtracker at https://bugs.launchpad.net/capturemock
