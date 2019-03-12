#!/usr/bin/env python
from distutils.core import setup
from distutils.command.install_scripts import install_scripts
import os, shutil

def make_windows_script(src):
    outFile = open(src + ".py", "w")
    outFile.write("#!python.exe\nimport site\n\n")
    outFile.write(open(src).read())

if os.name == "nt":
    package_data= { "capturemock" : [ "python_script.exe" ]}
else:
    package_data = {}

if os.name == "nt":
    make_windows_script("bin/capturemock")    
    scripts=["bin/capturemock.py"]
else:
    scripts=["bin/capturemock"]

class windows_install_scripts(install_scripts):
    """ Customized install_scripts distutils action.
    """

    def run(self):
        install_scripts.run(self)   # standard action
        shutil.copyfile("capturemock/python_script.exe", os.path.join(self.install_dir, "capturemock.exe"))

py_modules = []
if "FROM_RPM" not in os.environ:
    py_modules.append("ordereddict")

command_classes = {}
if os.name == "nt":
    command_classes['install_scripts'] = windows_install_scripts

setup(name='CaptureMock',
      version="trunk",
      author="Geoff Bache",
      author_email="geoff.bache@pobox.com",
      url="http://www.texttest.org/index.php?page=capturemock",
      description="A tool for creating mocks via a capture-replay style approach",
      long_description="CaptureMock's approach is a so-called capture-replay approach. This means that when you 'record' your mock, CaptureMock will observe the interaction between your code and the subsystem you are mocking out, and record it in a text file in its own format. When you then run your test in 'replay mode', CaptureMock can play the role of the subsystem in question and the real subsystem does not need to even be installed.\n\nYou can then choose, each time you run your tests, whether you wish to have the real subsystems present and verify/recreate the captured mocks, or to rely on the mocks captured by a previous run. If you are running in 'replay mode' and CaptureMock does not receive the same calls as previously, it will fail the test, and suggest that you may want to recreate the mocks in record mode.",
      packages=["capturemock"],
      py_modules=py_modules,
      package_data=package_data,
      classifiers=[ "Programming Language :: Python",
                    "License :: OSI Approved :: GNU Library or Lesser General Public License (LGPL)",
                    "Operating System :: OS Independent",
                    "Development Status :: 5 - Production/Stable",
                    "Environment :: Console",
                    "Intended Audience :: Developers",
                    "Intended Audience :: Information Technology",
                    "Topic :: Software Development :: Testing",
                    "Topic :: Software Development :: Libraries :: Python Modules" ],
      scripts=scripts,
      cmdclass=command_classes
      )
