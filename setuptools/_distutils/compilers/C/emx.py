"""distutils.emxccompiler

Provides the EMXCCompiler class, a subclass of UnixCCompiler that
handles the libc port of the GNU C compiler to OS/2.
"""

import copy
import os
import pathlib
import shlex
import sys
import warnings
from subprocess import check_output
from datetime import datetime

from ...errors import (
    DistutilsExecError,
    DistutilsPlatformError,
)
from ...file_util import write_file
from ...sysconfig import get_config_vars
from ...version import LooseVersion, suppress_known_deprecation
from . import unix
from .errors import (
    CompileError,
    Error,
)

class Compiler (unix.Compiler):
    """ Handles the libc port of the GNU C compiler to OS/2.
    """

    _rc_extensions = ['.rc', '.RC']
    compiler_type = 'emx'
    obj_extension = ".o"
    static_lib_extension = ".a"
    shared_lib_extension = "_dll.a"
    dylib_lib_extension = ".dll"
    static_lib_format = "lib%s%s"
    shared_lib_format = "lib%s%s"
    dylib_lib_format = "%s%s"
    res_extension = ".res"      # compiled resource file
    exe_extension = ".exe"

    def __init__(self, verbose=False, dry_run=False, force=False):
        super().__init__(verbose, dry_run, force)

        status, details = check_config_h()
        self.debug_print(f"Python's GCC status: {status} (details: {details})")
        if status is not CONFIG_H_OK:
            self.warn(
                "Python's pyconfig.h doesn't seem to support your compiler. "
                f"Reason: {details}. "
                "Compiling may fail because of undefined preprocessor macros."
            )

        self.cc, self.cxx = get_config_vars('CC', 'CXX')


    def _compile(self, obj, src, ext, cc_args, extra_postargs, pp_opts):
        """Compiles the source by spawning GCC and windres if needed."""
        if ext == '.rc':
            # gcc requires '.rc' compiled to binary ('.res') files !!!
            try:
                self.spawn(["rc", "-r", src])
            except DistutilsExecError as msg:
                raise CompileError(msg)
        else: # for other files use the C-compiler
            try:
                self.spawn(self.compiler_so + cc_args + [src, '-o', obj] +
                           extra_postargs)
            except DistutilsExecError as msg:
                raise CompileError(msg)

    def link(self, target_desc, objects, output_filename, output_dir=None,
             libraries=None, library_dirs=None, runtime_library_dirs=None,
             export_symbols=None, debug=False, extra_preargs=None,
             extra_postargs=None, build_temp=None, target_lang=None):
        """Link the objects."""
        # use separate copies, so we can modify the lists
        extra_preargs = copy.copy(extra_preargs or [])
        libraries = copy.copy(libraries or [])
        objects = copy.copy(objects or [])

        # Additional libraries
        libraries.extend(self.dll_libraries)

        # get dll/pyd name and extension
        (dll_name, dll_extension) = os.path.splitext(
           os.path.basename(output_filename))
        # if name is longer than 8 char, generate a hashed 8 char name
        if len(dll_name) > 8:
            dll_name8 = os.path.basename(output_filename)[:3] + str(reduce(lambda x,y:x+y, map(ord, output_filename)) % 65536)
        else:
            dll_name8 = dll_name

        # full relative path of dll/pyd
        dll_namefull = os.path.join(os.path.dirname(output_filename),
            dll_name8 + dll_extension)

        # handle export symbols by creating a def-file
        # with executables this only works with gcc/ld as linker
        if (export_symbols is not None and (
            target_desc != self.EXECUTABLE):
            # (The linker doesn't do anything if output is up-to-date.
            # So it would probably better to check if we really need this,
            # but for this we had to insert some unchanged parts of
            # UnixCCompiler, and this is not what we want.)

            # we want to put some files in the same directory as the
            # object files are, build_temp doesn't help much
            # where are the object files
            temp_dir = os.path.dirname(objects[0])

            # generate the filenames for these files
            def_file = os.path.join(temp_dir, dll_name + ".def")

            # prepare some needed values for the buildlevel
            vendor = os.getenv('VENDOR')
            if not vendor:
                vendor = "python build system"
            now = datetime.now()
            date_time = now.strftime("%d %b %Y %H:%M:%S")
            version = self.version
            if not version:
                version = "0.0"
            (osname, host, release, osversion, machine) = os.uname()

            # Generate .def file
            contents = [
                "LIBRARY %s INITINSTANCE TERMINSTANCE" % \
                dll_name8,
                "DESCRIPTION \"@#%s:%s#@##1## %s     %s::::0::@@%s\"" % \
                (vendor, version, date_time, host, dll_name),
                "DATA MULTIPLE NONSHARED",
                "EXPORTS"]

            for sym in export_symbols:
                contents.append('  "_%s"' % sym)
            self.execute(write_file, (def_file, contents),
                         "writing %s" % def_file)

            # next add options for def-file and to creating import libraries

            # for gcc/ld the def-file is specified as any other object files
            objects.append(def_file)

        # end: if ((export_symbols is not None) and
        #        (target_desc != self.EXECUTABLE or self.linker_dll == "gcc")):

        # who wants symbols and a many times larger output file
        # should explicitly switch the debug mode on
        # otherwise we let dllwrap/ld strip the output file
        # (On my machine: 10KB < stripped_file < ??100KB
        #   unstripped_file = stripped_file + XXX KB
        #  ( XXX=254 for a typical python extension))
        if not debug:
            extra_preargs.append("-s")

        super().link(target_desc, objects, dll_namefull,
                           output_dir, libraries, library_dirs,
                           runtime_library_dirs,
                           None, # export_symbols, we do this in our def-file
                           debug, extra_preargs, extra_postargs, build_temp,
                           target_lang)

        # if filename exceed 8 char, create a symlink to the 8 char dll/pyd
        if len(dll_name) > 8:
            try:
                os.remove(output_filename)
            except OSError:
                pass
            os.symlink(dll_name8 + dll_extension, output_filename)

    # -- Miscellaneous methods -----------------------------------------

    def object_filenames(self, source_filenames, strip_dir=0, output_dir=''):
        """Adds supports for rc and res files."""
        if output_dir is None:
            output_dir = ''
        obj_names = []
        for src_name in source_filenames:
            base, ext = os.path.splitext(src_name)
            base = os.path.splitdrive(base)[1] # Chop off the drive
            base = base[os.path.isabs(base):]  # If abs, chop off leading /
            if ext not in (self.src_extensions + self._rc_extensions):
                raise UnknownFileError("unknown file type '%s' (from '%s')" % \
                      (ext, src_name))
            if strip_dir:
                base = os.path.basename (base)
            if ext in self._rc_extensions:
                # these need to be compiled to object files
                obj_names.append (os.path.join(output_dir,
                                                base + self.res_extension))
            else:
                obj_names.append (os.path.join(output_dir,
                                                base + self.obj_extension))
        return obj_names

    # object_filenames ()

    # override the find_library_file method from UnixCCompiler
    # to deal with file naming/searching differences
    def find_library_file(self, dirs, lib, debug=0):
        try_names = [lib + ".lib", lib + ".a", lib + "_dll.a", "lib" + lib + ".lib", "lib" + lib + ".a", "lib" + lib + "_dll.a" ]

        # get EMX's default library directory search path
        try:
            emx_dirs = os.environ['LIBRARY_PATH'].split(';')
        except KeyError:
            emx_dirs = []

        for dir in dirs + emx_dirs:
            for name in try_names:
                libfile = os.path.join(dir, name)
                #print "libfile:",libfile
                if os.path.exists(libfile):
                    return libfile

        # Oops, didn't find it in *any* of 'dirs'
        return None

# class EMXCCompiler


# Because these compilers aren't configured in Python's pyconfig.h file by
# default, we should at least warn the user if he is using a unmodified
# version.

CONFIG_H_OK = "ok"
CONFIG_H_NOTOK = "not ok"
CONFIG_H_UNCERTAIN = "uncertain"

def check_config_h():
    """Check if the current Python installation appears amenable to building
    extensions with GCC.

    Returns a tuple (status, details), where 'status' is one of the following
    constants:

    - CONFIG_H_OK: all is well, go ahead and compile
    - CONFIG_H_NOTOK: doesn't look good
    - CONFIG_H_UNCERTAIN: not sure -- unable to read pyconfig.h

    'details' is a human-readable string explaining the situation.

    Note there are two ways to conclude "OK": either 'sys.version' contains
    the string "GCC" (implying that this Python was built with GCC), or the
    installed "pyconfig.h" contains the string "__GNUC__".
    """

    # XXX since this function also checks sys.version, it's not strictly a
    # "pyconfig.h" check -- should probably be renamed...

    from distutils import sysconfig

    # if sys.version contains GCC then python was compiled with
    # GCC, and the pyconfig.h file should be OK
    if "GCC" in sys.version:
        return CONFIG_H_OK, "sys.version mentions 'GCC'"

    # let's see if __GNUC__ is mentioned in python.h
    fn = sysconfig.get_config_h_filename()
    try:
        config_h = pathlib.Path(fn).read_text()
    except OSError as exc:
        return (CONFIG_H_UNCERTAIN, f"couldn't read '{fn}': {exc.strerror}")
    else:
        substring = '__GNUC__'
        if substring in config_h:
            code = CONFIG_H_OK
            mention_inflected = 'mentions'
        else:
            code = CONFIG_H_NOTOK
            mention_inflected = 'does not mention'
        return code, f"{fn!r} {mention_inflected} {substring!r}"

