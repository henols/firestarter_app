from setuptools import setup, find_packages

setup(
    name="firestarter",
    version="0.1",
    packages=find_packages(),
    include_package_data=True,
    install_requires=[
        "argparse",
        # Add other dependencies here
    ],
    entry_points={
        "console_scripts": [
            "firestarter=firestarter.main:main",
        ],
    },
    package_data={
        "firestarter": ["database.json", "pin-map.json"],
    },
    author="Henrik Olsson",
    author_email="henols@gmail.com",
    description="A brief description of your project",
    long_description=open("README.md").read(),
    long_description_content_type="text/markdown",
    url="https://github.com/henols/firestarter",
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    python_requires=">=3.6",
)
