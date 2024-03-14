#!/usr/bin/env python
from setuptools import setup
import os, shutil

if os.name == "nt":
    package_data= { "capturemock" : [ "capturemock_intercept.exe" ]}
else:
    package_data = {}

if os.name == "nt":
    shutil.copyfile("bin/capturemock", "bin/capturemock.py")    
    scripts=["bin/capturemock.py"]
else:
    scripts=["bin/capturemock"]

setup(name='CaptureMock',
      version="trunk",
      author="Geoff Bache",
      author_email="geoff.bache@pobox.com",
      url="http://www.texttest.org/index.php?page=capturemock",
      description="A tool for creating mocks via a capture-replay style approach",
      long_description="CaptureMock's approach is a so-called capture-replay approach. This means that when you 'record' your mock, CaptureMock will observe the interaction between your code and the subsystem you are mocking out, and record it in a text file in its own format. When you then run your test in 'replay mode', CaptureMock can play the role of the subsystem in question and the real subsystem does not need to even be installed.\n\nYou can then choose, each time you run your tests, whether you wish to have the real subsystems present and verify/recreate the captured mocks, or to rely on the mocks captured by a previous run. If you are running in 'replay mode' and CaptureMock does not receive the same calls as previously, it will fail the test, and suggest that you may want to recreate the mocks in record mode.",
      packages=["capturemock"],
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
      scripts=scripts
      )
