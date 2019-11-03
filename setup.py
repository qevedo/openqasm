import os
import setuptools

def read(fname):
	return open(os.path.join(os.path.dirname(__file__), fname)).read()

setuptools.setup(
	name="openqasm",
	version="0.0.1",
	author="Alex",
	author_email="alxmorais8@msn.com",
	description="Parser for OPENQASM",
	long_description=read("README.md"),
	long_description_content_type="text/markdown",
	url="https://github.com/qevedo/openqasm",
	license=read("LICENSE"),
	packages=["openqasm"],
	package_dir={"openqasm": "src"},
	python_requires=">=3.5",
)
