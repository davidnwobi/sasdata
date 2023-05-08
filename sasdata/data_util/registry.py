"""
File extension registry.

This provides routines for opening files based on extension,
and registers the built-in file extensions.
"""
from urllib.request import urlopen

from io import BytesIO, StringIO
from typing import Optional, List, Union
from collections import defaultdict

import h5py

from sasdata.data_util.loader_exceptions import NoKnownLoaderException
from sasdata.data_util.util import unique_preserve_order


class CustomFileOpen:
    """Custom context manager to fetch file contents."""
    def __init__(self, filename, mode='rb'):
        self.filename = filename
        self.mode = mode
        self.fd = None
        self.string_fd = None
        self.h5_fd = None

    def __enter__(self):
        """A context method that either fetches a file from a URL or opens a local file."""
        if '://' in self.filename:
            # Use urllib.request package to access remote files
            with urlopen(self.filename) as req:
                content = req.read()
                self.fd = BytesIO(content)
                h5_file = self.fd
        else:
            # Use native open to access local files
            self.fd = open(self.filename, self.mode)
            content = self.fd.read()
            h5_file = self.filename
        try:
            # H5PY uses its own reader that returns a dictionary-like data structure
            self.h5_fd = h5py.File(h5_file, 'r')
        except (TypeError, OSError):
            # Not an HDF5 file -> Ignore
            self.h5_fd = None
        try:
            # H5PY objects will fail here, but are unnecessary for later
            self.string_fd = StringIO(content.decode())
        except UnicodeDecodeError:
            self.string_fd = None
        # Return the instance to allow access to the filename, and any open file handles.
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Close all open file handles when exiting the context manager."""
        if self.fd is not None:
            self.fd.close()
        if self.h5_fd is not None:
            self.h5_fd.close()
        if self.string_fd is not None:
            self.string_fd.close()


class ExtensionRegistry:
    """
    Associate a file loader with an extension.

    Note that there may be multiple loaders for the same extension.

    Example: ::

        registry = ExtensionRegistry()

        # Add an association by setting an element
        registry['.zip'] = unzip

        # Multiple extensions for one loader
        registry['.tgz'] = untar
        registry['.tar.gz'] = untar

        # Generic extensions to use after trying more specific extensions;
        # these will be checked after the more specific extensions fail.
        registry['.gz'] = gunzip

        # Multiple loaders for one extension
        registry['.cx'] = cx1
        registry['.cx'] = cx2
        registry['.cx'] = cx3

        # Show registered extensions
        print registry.extensions()

        # Can also register a format name for explicit control from caller
        registry['cx3'] = cx3
        print registry.formats()

        # Retrieve loaders for a file name
        registry.lookup('hello.cx') -> [cx3,cx2,cx1]

        # Run loader on a filename
        registry.load('hello.cx') ->
            try:
                return cx3('hello.cx')
            except:
                try:
                    return cx2('hello.cx')
                except:
                    return cx1('hello.cx')

        # Load in a specific format ignoring extension
        registry.load('hello.cx',format='cx3') ->
            return cx3('hello.cx')
    """
    def __init__(self):
        self.readers = defaultdict(list)

    def __setitem__(self, ext: str, loader):
        self.readers[ext].insert(0, loader)

    def __getitem__(self, ext: str) -> List:
        return self.readers[ext]

    def __contains__(self, ext: str) -> bool:
        return ext in self.readers

    def formats(self) -> List[str]:
        """
        Return a sorted list of the registered formats.
        """
        names = [a for a in self.readers.keys() if not a.startswith('.')]
        names.sort()
        return names

    def extensions(self) -> List[str]:
        """
        Return a sorted list of registered extensions.
        """
        exts = [a for a in self.readers.keys() if a.startswith('.')]
        exts.sort()
        return exts

    def lookup(self, path: str) -> List[str]:
        """
        Return the loader associated with the file type of path.

        :param path: Data file path
        :return: List of available readers for the file extension (maybe empty)
        """
        # Find matching lower-case extensions
        path_lower = path.lower()
        extensions = [ext for ext in self.extensions() if path_lower.endswith(ext)]
        # Sort matching extensions by decreasing order of length
        extensions.sort(key=len)
        # Combine readers for matching extensions into one big list
        readers = [reader for ext in extensions for reader in self.readers[ext]]
        return unique_preserve_order(readers)

    def load(self, path: str, ext: Optional[str] = None) -> Union[List, Exception]:
        """
        Call the loader for the file type of path.

        Raises an exception if the loader fails or if no loaders are defined
        for the given path or format.
        """
        if ext is None:
            loaders = self.lookup(path)
            if not loaders:
                raise NoKnownLoaderException("No loaders match extension in %r"
                                             % path)
        else:
            loaders = self.readers.get(ext.lower(), [])
            if not loaders:
                raise NoKnownLoaderException("No loaders match format %r"
                                             % ext)
        last_exc = None
        with CustomFileOpen(path, 'rb') as file_handler:
            for load_function in loaders:
                try:
                    return load_function(file_handler.filename, file_handler)
                except Exception as e:
                    last_exc = e
                    pass  # give other loaders a chance to succeed
            # If we get here it is because all loaders failed
            raise last_exc
