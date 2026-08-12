from setuptools import setup, find_packages

setup(
    name="economy-discord-bot",
    version="1.0.0",
    description="A comprehensive Discord economy bot with stock market simulation",
    author="Your Name",
    author_email="your.email@example.com",
    packages=find_packages(),
    install_requires=[
        "discord.py>=2.3.0",
        "python-dotenv>=1.0.0",
        "aiohttp>=3.8.0",
        "matplotlib>=3.7.0",
    ],
    python_requires=">=3.8",
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: End Users/Desktop",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
    ],
)