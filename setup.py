from setuptools import find_packages, setup

setup(
    name="mcp_frappe",
    version="0.1.0",
    description="Universal MCP server app for Frappe Framework",
    packages=find_packages(),
    include_package_data=True,
    install_requires=[
        "jsonschema>=4.24,<5.0",
        "Click>=8.1.8,<9.0",
    ],
    zip_safe=False,
)
