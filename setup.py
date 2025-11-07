"""Setup configuration for doc_fetcher package."""

from setuptools import setup, find_packages
from pathlib import Path

# Read the README file
readme_file = Path(__file__).parent / "README.md"
long_description = readme_file.read_text(encoding="utf-8") if readme_file.exists() else ""

setup(
    name="docpull",
    version="1.0.0",
    author="Zachary Roth",
    author_email="support@raintree.technology",
    description="Pull documentation from the web and convert to clean markdown",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/Raintree-Technology/docpull",
    packages=find_packages(),
    classifiers=[
        # Development Status
        "Development Status :: 4 - Beta",
        # Intended Audience
        "Intended Audience :: Developers",
        "Intended Audience :: Information Technology",
        "Intended Audience :: Science/Research",
        # Environment
        "Environment :: Console",
        # Topic
        "Topic :: Documentation",
        "Topic :: Internet :: WWW/HTTP :: Indexing/Search",
        "Topic :: Software Development :: Documentation",
        "Topic :: Text Processing :: Markup :: HTML",
        "Topic :: Text Processing :: Markup :: Markdown",
        "Topic :: Utilities",
        # Natural Language
        "Natural Language :: English",
        # Operating System
        "Operating System :: OS Independent",
        # Programming Language
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Programming Language :: Python :: 3.13",
        "Programming Language :: Python :: 3 :: Only",
        # Typing
        "Typing :: Typed",
    ],
    python_requires=">=3.8",
    install_requires=[
        "requests>=2.31.0",
        "beautifulsoup4>=4.12.0",
        "html2text>=2020.1.16",
    ],
    extras_require={
        "yaml": ["pyyaml>=6.0"],
        "dev": [
            "pytest>=7.0.0",
            "black>=23.0.0",
            "mypy>=1.0.0",
            "ruff>=0.1.0",
        ],
    },
    entry_points={
        "console_scripts": [
            "docpull=doc_fetcher.cli:main",
        ],
    },
)
