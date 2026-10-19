from setuptools import find_packages, setup

setup(
    name="mcp-frappe",
    version="0.2.0",
    description="Standalone MCP server for Frappe Framework sites",
    packages=find_packages(),
    include_package_data=True,
    install_requires=["httpx>=0.27,<1.0", "mcp>=1.26,<2.0"],
    zip_safe=False,
)
