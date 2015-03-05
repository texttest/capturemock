#
# spec file for package python-CaptureMock
#
# Copyright (c) 2015 SUSE LINUX Products GmbH, Nuernberg, Germany.
#
# All modifications and additions to the file contributed by third parties
# remain the property of their copyright owners, unless otherwise agreed
# upon. The license for this file, and modifications and additions to the
# file, is the same license as for the pristine package itself (unless the
# license for the pristine package is not an Open Source License, in which
# case the license is the MIT License). An "Open Source License" is a
# license that conforms to the Open Source Definition (Version 1.9)
# published by the Open Source Initiative.

# Please submit bugfixes or comments via http://bugs.opensuse.org/


Name:           python-CaptureMock
Version:        trunk
Release:        0
License:        LGPL
Summary:        A tool for creating mocks via a capture-replay style approach
Url:            http://www.texttest.org/index.php?page=capturemock
Group:          Development/Languages/Python
Source:         https://pypi.python.org/packages/source/C/CaptureMock/CaptureMock-%{version}.tar.gz
Requires:       python-ordereddict
BuildRequires:  python-devel
BuildRoot:      %{_tmppath}/%{name}-%{version}-build
%if 0%{?suse_version} && 0%{?suse_version} <= 1110
%{!?python_sitelib: %global python_sitelib %(python -c "from distutils.sysconfig import get_python_lib; print get_python_lib()")}
%else
BuildArch:      noarch
%endif

%description
CaptureMock's approach is a so-called capture-replay approach. This means that when you 'record' your mock, CaptureMock will observe the interaction between your code and the subsystem you are mocking out, and record it in a text file in its own format. When you then run your test in 'replay mode', CaptureMock can play the role of the subsystem in question and the real subsystem does not need to even be installed.

You can then choose, each time you run your tests, whether you wish to have the real subsystems present and verify/recreate the captured mocks, or to rely on the mocks captured by a previous run. If you are running in 'replay mode' and CaptureMock does not receive the same calls as previously, it will fail the test, and suggest that you may want to recreate the mocks in record mode.

%prep
%setup -q -n CaptureMock-%{version}

%build
env FROM_RPM=1 python setup.py build

%install
env FROM_RPM=1 python setup.py install --prefix=%{_prefix} --root=%{buildroot}

%files
%defattr(-,root,root,-)
%doc README.txt
%{_bindir}/capturemock
%{python_sitelib}/*

%changelog
