"""Setup for cli-anything-meican."""

from setuptools import find_namespace_packages, setup

setup(
    name="cli-anything-meican",
    version="0.1.0",
    description="CLI harness for meican (美餐) website",
    packages=find_namespace_packages(include=["cli_anything.*"]),
    python_requires=">=3.8",
    install_requires=[
        "click>=8.0",
        "requests>=2.28",
    ],
    entry_points={
        "console_scripts": [
            "cli-anything-meican=cli_anything.meican.meican_cli:main",
        ],
    },
)
