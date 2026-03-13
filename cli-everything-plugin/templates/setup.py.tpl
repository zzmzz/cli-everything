"""Setup for cli-anything-{{ site_name }}."""

from setuptools import find_namespace_packages, setup

setup(
    name="cli-anything-{{ site_name }}",
    version="0.1.0",
    description="CLI harness for {{ site_name }} website",
    packages=find_namespace_packages(include=["cli_anything.*"]),
    python_requires=">=3.10",
    install_requires=[
        "click>=8.0",
        "requests>=2.28",
    ],
    entry_points={
        "console_scripts": [
            "cli-anything-{{ site_name }}=cli_anything.{{ site_name }}.{{ site_name }}_cli:main",
        ],
    },
)
