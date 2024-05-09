from setuptools import setup, find_packages

setup(
    name='vital-agent-container-client',
    version='0.0.1',
    author='Marc Hadfield',
    author_email='marc@vital.ai',
    description='Vital Agent Container Client',
    long_description=open('README.md').read(),
    long_description_content_type='text/markdown',
    url='https://github.com/vital-ai/vital-agent-container-client',
    packages=find_packages(exclude=["test"]),
    license='Apache License 2.0',
    install_requires=[
            'vital-ai-vitalsigns>=0.1.10',
            'vital-ai-domain>=0.1.4',
            'six',
            'pyyaml'
    ],
    classifiers=[
        "Programming Language :: Python :: 3.10",
        "License :: OSI Approved :: Apache Software License",
        "Operating System :: OS Independent",
    ],
    python_requires='>=3.10',
)
