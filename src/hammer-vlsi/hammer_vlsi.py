#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
#  hammer_vlsi.py
#
#  Copyright 2017 Edward Wang <edward.c.wang@compdigitec.com>

from abc import ABCMeta, abstractmethod
from enum import Enum

from typing import Callable, Iterable, List, NamedTuple, Tuple, TypeVar, Type, Optional, Dict, Any

from functools import reduce

import datetime
import importlib
import os
import re
import shlex
import subprocess
import sys

import hammer_config

class Level(Enum):
    """
    Logging levels.
    """
    # Explanation of logging levels:
    # DEBUG - for debugging only, too verbose for general use
    # INFO - general informational messages (e.g. "starting synthesis")
    # WARNING - things the user should check out (e.g. a setting uses something very usual)
    # ERROR - something gone wrong but the process can still continue (e.g. synthesis run failed), though the user should definitely check it out.
    # FATAL - an error which will abort the process immediately without warning (e.g. assertion failure)
    DEBUG = 0
    INFO = 1
    WARNING = 2
    ERROR = 3
    FATAL = 4

# Message including additional metadata such as level and context.
FullMessage = NamedTuple('FullMessage', [
    ('message', str),
    ('level', Level),
    ('context', List[str])
])

# Need a way to bind the callbacks to the class...
def with_default_callbacks(cls):
    # TODO: think about how to remove default callbacks
    cls.add_callback(cls.callback_print)
    cls.add_callback(cls.callback_buffering)
    return cls

class HammerVLSIFileLogger:
    """A file logger for HammerVLSILogging."""

    def __init__(self, output_path: str, format_msg_callback: Callable[[FullMessage], str] = None) -> None:
        """
        Create a new file logger.

        :param output_path: Output path of the logger.
        :param format_msg_callback: Optional callback to run to build the message. None to use HammerVLSILogging.build_log_message.
        """
        self._file = open(output_path, "a")
        self._format_msg_callback = format_msg_callback

    def __enter__(self):
        return self

    def close(self) -> None:
        """
        Close this file logger.
        """
        self._file.close()

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    @property
    def callback(self) -> Callable[[FullMessage], None]:
        """Get the callback for HammerVLSILogging.add_callback."""
        def file_callback(fullmessage: FullMessage) -> None:
            if self._format_msg_callback is not None:
                self._file.write(self._format_msg_callback(fullmessage) + "\n")
            else:
                self._file.write(HammerVLSILogging.build_log_message(fullmessage) + "\n")
        return file_callback

@with_default_callbacks
class HammerVLSILogging:
    """Singleton which handles logging in hammer-vlsi.

    This class is generally not intended to be used directly for logging, but through HammerVLSILoggingContext instead.
    """

    # Enable colour in logging?
    enable_colour = True # type: bool

    # If buffering is enabled, instead of printing right away, we will store it
    # into a buffer for later retrival.
    enable_buffering = False # type: bool

    # Enable printing the tag (e.g. "[synthesis] ...).
    enable_tag = True # type: bool

    # Various escape characters for colour output.
    COLOUR_BLUE = "\033[96m"
    COLOUR_GREY = "\033[37m"
    COLOUR_YELLOW = "\033[33m"
    COLOUR_RED = "\033[91m"
    COLOUR_RED_BG = "\033[101m"
    # Restore the default terminal colour.
    COLOUR_CLEAR = "\033[0m"

    # Some default callback implementations.
    @classmethod
    def callback_print(cls, fullmessage: FullMessage) -> None:
        """Default callback which prints a colour message."""
        print(cls.build_message(fullmessage))

    output_buffer = [] # type: List[str]
    @classmethod
    def callback_buffering(cls, fullmessage: FullMessage) -> None:
        """Get the current contents of the logging buffer and clear it."""
        if not cls.enable_buffering:
            return
        cls.output_buffer.append(cls.build_message(fullmessage))

    @classmethod
    def build_log_message(cls, fullmessage: FullMessage) -> str:
        """Build a plain message for logs, without colour."""
        template = "{context} {level}: {message}"

        return template.format(context=cls.get_tag(fullmessage.context), level=fullmessage.level, message=fullmessage.message)

    # List of callbacks to call for logging.
    callbacks = [] # type: List[Callable[[FullMessage], None]]

    @classmethod
    def clear_callbacks(cls) -> None:
        """Clear the list of callbacks."""
        cls.callbacks = []

    @classmethod
    def add_callback(cls, callback: Callable[[FullMessage], None]) -> None:
        """Add a callback."""
        cls.callbacks.append(callback)

    @classmethod
    def context(cls, new_context: str = "") -> "HammerVLSILoggingContext":
        """
        Create a new context.

        :param new_context: Context name. Leave blank to get the global context.
        """
        if new_context == "":
            return HammerVLSILoggingContext([], cls)
        else:
            return HammerVLSILoggingContext([new_context], cls)

    @classmethod
    def get_colour_escape(cls, level: Level) -> str:
        """Colour table to translate level -> colour in logging."""
        table = {
            Level.DEBUG: cls.COLOUR_GREY,
            Level.INFO: cls.COLOUR_BLUE,
            Level.WARNING: cls.COLOUR_YELLOW,
            Level.ERROR: cls.COLOUR_RED,
            Level.FATAL: cls.COLOUR_RED_BG
        }
        if level in table:
            return table[level]
        else:
            return ""

    @classmethod
    def log(cls, fullmessage: FullMessage) -> None:
        """
        Log the given message at the given level in the given context.
        """
        for callback in cls.callbacks:
            callback(fullmessage)

    @classmethod
    def build_message(cls, fullmessage: FullMessage) -> str:
        """Build a colour message."""
        message = fullmessage.message
        level = fullmessage.level
        context = fullmessage.context

        context_tag = cls.get_tag(context) # type: str

        output = "" # type: str
        output += cls.get_colour_escape(level) if cls.enable_colour else ""
        if cls.enable_tag and context_tag != "":
            output += context_tag + " "
        output += message
        output += cls.COLOUR_CLEAR if cls.enable_colour else ""

        return output

    @staticmethod
    def get_tag(context: List[str]) -> str:
        """Helper function to get the tag for outputing a message given a context."""
        if len(context) > 0:
            return str(reduce(lambda a, b: a + " " + b, map(lambda x: "[%s]" % (x), context)))
        else:
            return "[<global>]"

    @classmethod
    def get_buffer(cls) -> Iterable[str]:
        """Get the current contents of the logging buffer and clear it."""
        if not cls.enable_buffering:
            raise ValueError("Buffering is not enabled")
        output = list(cls.output_buffer)
        cls.output_buffer = []
        return output

VT = TypeVar('VT', bound='HammerVLSILoggingContext')
class HammerVLSILoggingContext:
    """
    Logging interface to hammer-vlsi which contains a context (list of strings denoting hierarchy where the log occurred).
    e.g. ["synthesis", "subprocess run-synthesis"]
    """
    def __init__(self, context: List[str], logging_class: Type[HammerVLSILogging]) -> None:
        """
        Create a new interface with the given context.
        """
        self._context = context # type: List[str]
        self.logging_class = logging_class # type: Type[HammerVLSILogging]

    def context(self: VT, new_context: str) -> VT:
        """
        Create a new subcontext from this context.
        """
        context2 = list(self._context)
        context2.append(new_context)
        return HammerVLSILoggingContext(context2, self.logging_class)

    def debug(self, message: str) -> None:
        """Create an debug-level log message."""
        return self.log(message, Level.DEBUG)

    def info(self, message: str) -> None:
        """Create an info-level log message."""
        return self.log(message, Level.INFO)

    def warning(self, message: str) -> None:
        """Create an warning-level log message."""
        return self.log(message, Level.WARNING)

    def error(self, message: str) -> None:
        """Create an error-level log message."""
        return self.log(message, Level.ERROR)

    def fatal(self, message: str) -> None:
        """Create an fatal-level log message."""
        return self.log(message, Level.FATAL)

    def log(self, message: str, level: Level) -> None:
        return self.logging_class.log(FullMessage(message, level, self._context))

import hammer_tech

class HammerVLSISettings:
    """
    Static class which holds global hammer-vlsi settings.
    """
    hammer_vlsi_path = "" # type: str

    @staticmethod
    def get_config() -> dict:
        """Export settings as a config dictionary."""
        return {
            "vlsi.builtins.hammer_vlsi_path": HammerVLSISettings.hammer_vlsi_path
        }

class TimeValue:
    """Time value - e.g. "4 ns".
    Parses time values from strings.
    """

    # From https://stackoverflow.com/a/10970888
    _prefix_table = {
        'y': 1e-24,  # yocto
        'z': 1e-21,  # zepto
        'a': 1e-18,  # atto
        'f': 1e-15,  # femto
        'p': 1e-12,  # pico
        'n': 1e-9,   # nano
        'u': 1e-6,   # micro
        'm': 1e-3,   # mili
        'c': 1e-2,   # centi
        'd': 1e-1,   # deci
        'k': 1e3,    # kilo
        'M': 1e6,    # mega
        'G': 1e9,    # giga
        'T': 1e12,   # tera
        'P': 1e15,   # peta
        'E': 1e18,   # exa
        'Z': 1e21,   # zetta
        'Y': 1e24,   # yotta
    }

    def __init__(self, value: str, default_prefix: str = 'n') -> None:
        """Create a time value from parsing the given string.
        Default prefix: ns
        """
        import re

        regex = r"^([\d.]+) *(.*)s$"
        m = re.search(regex, value)
        if m is None:
            try:
                num = str(float(value))
                prefix = default_prefix
            except ValueError:
                raise ValueError("Malformed time value %s" % (value))
        else:
            num = m.group(1)
            prefix = m.group(2)

        if num.count('.') > 1 or len(prefix) > 1:
            raise ValueError("Malformed time value %s" % (value))

        if prefix not in self._prefix_table:
            raise ValueError("Bad prefix for %s" % (value))

        self._value = float(num) # type: float
        # Preserve the prefix too to preserve precision
        self._prefix = self._prefix_table[prefix] # type: float

    @property
    def value(self) -> float:
        """Get the value of this time value."""
        return self._value * self._prefix

    def value_in_units(self, prefix: str, round_zeroes: bool = True) -> float:
        """Get this time value in the given prefix. e.g. "ns"
        """
        retval = self._value * (self._prefix / self._prefix_table[prefix[0]])
        if round_zeroes:
            return round(retval, 2)
        else:
            return retval

    def str_value_in_units(self, prefix: str, round_zeroes: bool = True) -> str:
        """Get this time value in the given prefix but including the units.
        e.g. return "5 ns".

        :param prefix: Prefix for the resulting value - e.g. "ns".
        :param round_zeroes: True to round 1.00000001 etc to 1 within 2 decimal places.
        """
        # %g removes trailing zeroes
        return "%g" % (self.value_in_units(prefix, round_zeroes)) + " " + prefix

ClockPort = NamedTuple('ClockPort', [
    ('name', str),
    ('period', TimeValue),
    ('uncertainty', Optional[TimeValue])
])

# Library filter containing a filtering function, identifier tag, and a
# short human-readable description.
class LibraryFilter(NamedTuple('LibraryFilter', [
    ('func', Callable[[hammer_tech.Library], List[str]]),
    ('tag', str),
    ('description', str),
    ('is_file', bool), # Is the generated filter intended to be a file?
    ('extra_post_filter_funcs', List[Callable[[List[str]], List[str]]]) # List of functions to call on the list-level (the list of elements generated by func) before output and post-processing.
])):
    __slots__ = ()
    def __new__(cls, func, tag, description, is_file, extra_post_filter_funcs = []):
        return super(LibraryFilter, cls).__new__(LibraryFilter, func, tag, description, is_file, list(extra_post_filter_funcs))

class HammerTool(metaclass=ABCMeta):
    # Interface methods.
    @abstractmethod
    def do_run(self) -> bool:
        """Run the tool after the setup.

        :return: True if the tool finished successfully; false otherwise.
        """
        pass

    @property
    def env_vars(self) -> Dict[str, str]:
        """
        Get the list of environment variables required for this tool.
        Note to subclasses: remember to include variables from super().env_vars!

        :return: Mapping of environment variable -> contents of said variable.
        """
        return {}

    # Setup functions.
    def run(self) -> bool:
        """Run this tool.

        Perform some setup operations to set up the config and tool environment, and then
        calls do_run() to invoke the tool-specific actions.

        :return: True if the tool finished successfully; false otherwise.
        """

        # Ensure that the run_dir exists.
        os.makedirs(self.run_dir, exist_ok=True)

        return self.do_run()

    @property
    def _subprocess_env(self) -> dict:
        """
        Internal helper function to set the environment variables for
        self.run_executable().
        """
        env = os.environ.copy()
        # Add HAMMER_DATABASE to the environment for the script.
        env.update({"HAMMER_DATABASE": self.dump_database()})
        env.update(self.env_vars)
        return env

    # Properties.
    @property
    def name(self) -> str:
        """
        Short name of the tool library.
        Typically the folder name (e.g. "dc", "yosys", etc).

        :return: Short name of the tool library.
        """
        try:
            return self._name
        except AttributeError:
            raise ValueError("Internal error: Short name of the tool library not set by hammer-vlsi")

    @name.setter
    def name(self, value: str) -> None:
        """Set the Short name of the tool library."""
        self._name = value # type: str

    @property
    def tool_dir(self) -> str:
        """
        Get the location of the tool library.

        :return: Path to the location of the library.
        """
        try:
            return self._tooldir
        except AttributeError:
            raise ValueError("Internal error: tool dir location not set by hammer-vlsi")

    @tool_dir.setter
    def tool_dir(self, value: str) -> None:
        """Set the directory which contains this tool library."""
        self._tooldir = value # type: str

    @property
    def run_dir(self) -> str:
        """
        Get the location of the run dir, a writable temporary information for use by the tool.

        :return: Path to the location of the library.
        """
        try:
            return self._rundir
        except AttributeError:
            raise ValueError("Internal error: run dir location not set by hammer-vlsi")

    @run_dir.setter
    def run_dir(self, value: str) -> None:
        """Set the location of a writable directory which the tool can use to store temporary information."""
        self._rundir = value # type: str

    @property
    def input_files(self) -> Iterable[str]:
        """
        Input files for this tool library.
        The exact nature of the files will depend on the type of library.
        """
        try:
            return self._input_files
        except AttributeError:
            raise ValueError("Nothing set for inputs yet")

    @input_files.setter
    def input_files(self, value: Iterable[str]) -> None:
        """
        Set the input files for this tool library.
        The exact nature of the files will depend on the type of library.
        """
        if not isinstance(value, Iterable):
            raise TypeError("input_files must be a Iterable[str]")
        self._input_files = value # type: Iterable[str]

    @property
    def technology(self) -> hammer_tech.HammerTechnology:
        """
        Get the technology library currently in use.

        :return: HammerTechnology instance
        """
        try:
            return self._technology
        except AttributeError:
            raise ValueError("Internal error: technology not set by hammer-vlsi")

    @technology.setter
    def technology(self, value: hammer_tech.HammerTechnology) -> None:
        """Set the HammerTechnology currently in use."""
        self._technology = value # type: hammer_tech.HammerTechnology

    @property
    def logger(self) -> HammerVLSILoggingContext:
        """Get the logger for this tool."""
        try:
            return self._logger
        except AttributeError:
            raise ValueError("Internal error: logger not set by hammer-vlsi")

    @logger.setter
    def logger(self, value: HammerVLSILoggingContext) -> None:
        """Set the logger for this tool."""
        self._logger = value # type: HammerVLSILoggingContext

    # Accessory functions available to tools.
    # TODO(edwardw): maybe move set_database/get_setting into an interface like UsesHammerDatabase?
    def set_database(self, database: hammer_config.HammerDatabase) -> None:
        """Set the settings database for use by the tool."""
        self._database = database # type: hammer_config.HammerDatabase

    def dump_database(self) -> str:
        """Dump the current database JSON in a temporary file in the run_dir and return the path.
        """
        path = os.path.join(self.run_dir, "config_db_tmp.json")
        db_contents = self._database.get_database_json()
        with open(path, 'w') as f:
            f.write(db_contents)
        return path

    def get_config(self) -> List[dict]:
        """Get the config for this tool."""
        return hammer_config.load_config_from_defaults(self.tool_dir)

    def get_setting(self, key: str, nullvalue: Optional[str] = None):
        """
        Get a particular setting from the database.
        :param key: Key of the setting to receive.
        :param nullvalue: Value to return in case of null (leave as None to use the default).
        """
        try:
            if nullvalue is None:
                return self._database.get_setting(key)
            else:
                return self._database.get_setting(key, nullvalue)
        except AttributeError:
            raise ValueError("Internal error: no database set by hammer-vlsi")

    def create_enter_script(self, enter_script_location: str = "", raw: bool = False) -> None:
        """
        Create the enter script inside the rundir which can be used to
        create an interactive environment with all the same variables
        used to launch this tool.

        :param enter_script_location: Location to create the enter script. Defaults to self.run_dir + "/enter"
        :param raw: Emit the raw string without shell escaping (without quotes!!!)
        """
        def escape_value(val: str) -> str:
            if raw:
                return val
            else:
                if val == "":
                    return '""'
                quoted = shlex.quote(val) # type: str
                # For readability e.g. export X="9" vs export X=9
                if quoted == val:
                    return '"' + val + '"'
                else:
                    return quoted

        if enter_script_location == "":
            enter_script_location = os.path.join(self.run_dir, "enter")
        enter_script = "\n".join(map(lambda k_v: "export {0}={1}".format(k_v[0], escape_value(k_v[1])), sorted(self.env_vars.items())))
        with open(enter_script_location, "w") as f:
            f.write(enter_script)

    def check_input_files(self, extensions: List[str]) -> bool:
        """Verify that input files exist and have the specified extensions.

        :param extensions: List of extensions e.g. [".v", ".sv"]
        :return: True if all files exist and have the specified extensions.
        """
        verilog_args = self.input_files
        error = False
        for v in verilog_args:
            if not v.endswith(tuple(extensions)):
                self.logger.error("Input of unsupported type {0} detected!".format(v))
                error = True
            if not os.path.isfile(v):
                self.logger.error("Input file {0} does not exist!".format(v))
                error = True
        return not error

    # TODO: should some of these live in hammer_tech instead?
    def filter_and_select_libs(self, func: Callable[[hammer_tech.Library], List[str]],
                               extra_funcs: List[Callable[[str], str]] = [],
                               lib_filters: List[Callable[[hammer_tech.Library], bool]] = []) -> List[str]:
        """
        Generate a list by filtering the list of libraries and selecting some parts of it.

        :param func: Function to call to extract the desired component of the lib.
        :param extra_funcs: List of extra functions to call before wrapping them in the arg prefixes.
        :param lib_filters: Filters to filter the list of libraries before selecting desired results from them.
        :return: List of arguments to pass to a shell script
        """
        filtered_libs = reduce_named(
            sequence=lib_filters,
            initial=self.technology.config.libraries,
            function=lambda libs, func: filter(func, libs)
        )

        lib_results = list(reduce(lambda a, b: a+b, list(map(func, filtered_libs)))) # type: List[str]

        # Uniquify results.
        # TODO: think about whether this really belongs here and whether we always need to uniquify.
        # This is here to get stuff working since some CAD tools dislike duplicated arguments (e.g. duplicated stdcell lib, etc).
        lib_results = list(set(lib_results)) # type: List[str]

        lib_results_with_extra_funcs = reduce(lambda arr, func: map(func, arr), extra_funcs, lib_results)

        return list(lib_results_with_extra_funcs)

    def filter_for_supplies(self, lib: hammer_tech.Library) -> bool:
        """Function to help filter a list of libraries to find libraries which have matching supplies.
        Will also use libraries with no supplies annotation.

        :param lib: Library to check
        :return: True if the supplies of this library match the inputs for this run, False otherwise.
        """
        if lib.supplies is None:
            # TODO: add some sort of wildcard value for supplies for libraries which _actually_ should
            # always be used.
            self.logger.warning("Lib %s has no supplies annotation! Using anyway." % (lib.serialize()))
            return True
        return self.get_setting("vlsi.inputs.supplies.VDD") == lib.supplies.VDD and self.get_setting("vlsi.inputs.supplies.GND") == lib.supplies.GND

    @staticmethod
    def make_check_isdir(description: str = "Path") -> Callable[[str], str]:
        """
        Utility function to generate functions which check whether a path exists.
        """
        def check_isdir(path: str) -> str:
            if not os.path.isdir(path):
                raise ValueError("%s %s is not a directory or does not exist" % (description, path))
            else:
                return path
        return check_isdir

    @staticmethod
    def make_check_isfile(description: str = "File") -> Callable[[str], str]:
        """
        Utility function to generate functions which check whether a path exists.
        """
        def check_isfile(path: str) -> str:
            if not os.path.isfile(path):
                raise ValueError("%s %s is not a file or does not exist" % (description, path))
            else:
                return path
        return check_isfile

    @staticmethod
    def replace_tcl_set(variable: str, value: str, tcl_path: str, quotes: bool = True) -> None:
        """
        Utility function to replaces a "set VARIABLE ..." line with set VARIABLE
        "value" in the given TCL script file.

        :param variable: Variable name to replace
        :param value: Value to replace it with (default quoted)
        :param tcl_path: Path to the TCL script.
        :param quotes: (optional) Set to False to disable quoting of the value.
        """
        with open(tcl_path, "r") as f:
            tcl_contents = f.read() # type: str

        value_string = value
        if quotes:
            value_string = '"' + value_string + '"'
        replacement_string = "set %s %s;" % (variable, value_string)

        regex = r'^set +%s.*' % (re.escape(variable))
        if re.search(regex, tcl_contents, flags=re.MULTILINE) is None:
            raise ValueError("set %s line not found in tcl file %s!" % (variable, tcl_path))

        new_tcl_contents = re.sub(regex, replacement_string, tcl_contents, flags=re.MULTILINE) # type: str

        with open(tcl_path, "w") as f:
            f.write(new_tcl_contents)

    # TODO(edwardw): consider pulling this out so that hammer_tech can also use this
    def run_executable(self, args: List[str], cwd: str = None) -> str:
        """
        Run an executable and log the command to the log while also capturing the output.

        :param args: Command-line to run; each item in the list is one token. The first token should be the command to run.
        :param cwd: Working directory (leave as None to use the current working directory).
        :return: Output from the command or an error message.
        """
        self.logger.debug("Executing subprocess: " + ' '.join(args))

        # Short version for easier display in the log.
        PROG_NAME_LEN = 14 # Capture last 14 characters of the command name
        if len(args[0]) <= PROG_NAME_LEN:
            prog_name = args[0]
        else:
            prog_name = "..." + args[0][len(args[0])-PROG_NAME_LEN:]
        remaining_args = " ".join(args[1:])
        if len(remaining_args) <= 15:
            prog_args = remaining_args
        else:
            prog_args = remaining_args[0:15] + "..."
        prog_tag = prog_name + " " + prog_args
        subprocess_logger = self.logger.context("Exec " + prog_tag)

        proc = subprocess.Popen(args, shell=False, stderr=subprocess.STDOUT, stdout=subprocess.PIPE, env=self._subprocess_env, cwd=cwd)
        # Log output and also capture output at the same time.
        output_buf = ""
        while True:
            line = proc.stdout.readline().decode("utf-8")
            if line != '':
                subprocess_logger.debug(line.rstrip())
                output_buf += line
            else:
                break
        # TODO: check errors
        return line

    # Common convenient filters useful to many different tools.
    @staticmethod
    def create_nonempty_check(description: str) -> Callable[[List[str]], List[str]]:
        def check_nonempty(l: List[str]) -> List[str]:
            if len(l) == 0:
                raise ValueError("Must have at least one " + description)
            else:
                return l
        return check_nonempty

    @staticmethod
    def to_command_line_args(lib_item: str, filt: LibraryFilter) -> List[str]:
        """
        Generate command-line args in the form --<filt.tag> <lib_item>.
        """
        return ["--" + filt.tag, lib_item]

    @staticmethod
    def to_plain_item(lib_item: str, filt: LibraryFilter) -> List[str]:
        """
        Generate plain outputs in the form of <lib_item1> <lib_item2> ...
        """
        return [lib_item]

    @property
    def timing_db_filter(self) -> LibraryFilter:
        """
        Selecting Synopsys timing libraries (.db). Prefers CCS if available; picks NLDM as a fallback.
        """
        def select_func(lib: hammer_tech.Library) -> List[str]:
            # Choose ccs if available, if not, nldm.
            if lib.ccs_library_file is not None:
                return [lib.ccs_library_file]
            elif lib.nldm_library_file is not None:
                return [lib.nldm_library_file]
            else:
                return []
        return LibraryFilter(select_func, "timing_db", "CCS/NLDM timing lib (Synopsys .db)", is_file=True)

    @property
    def liberty_lib_filter(self) -> LibraryFilter:
        """
        Selecting ASCII liberty (.lib) libraries. Prefers CCS if available; picks NLDM as a fallback.
        """
        def select_func(lib: hammer_tech.Library) -> List[str]:
            # Choose ccs if available, if not, nldm.
            if lib.ccs_liberty_file is not None:
                return [lib.ccs_liberty_file]
            elif lib.nldm_liberty_file is not None:
                return [lib.nldm_liberty_file]
            else:
                return []
        return LibraryFilter(select_func, "timing_lib", "CCS/NLDM timing lib (liberty ASCII .lib)", is_file=True)

    @property
    def milkyway_lib_dir_filter(self) -> LibraryFilter:
        def select_milkyway_lib(lib: hammer_tech.Library) -> List[str]:
            if lib.milkyway_lib_in_dir is not None:
                return [os.path.dirname(lib.milkyway_lib_in_dir)]
            else:
                return []
        return LibraryFilter(select_milkyway_lib, "milkyway_dir", "Milkyway lib", is_file=False)

    @property
    def milkyway_techfile_filter(self) -> LibraryFilter:
        """Select milkyway techfiles."""
        def select_milkyway_tfs(lib: hammer_tech.Library) -> List[str]:
            if lib.milkyway_techfile is not None:
                return [lib.milkyway_techfile]
            else:
                return []
        return LibraryFilter(select_milkyway_tfs, "milkyway_tf", "Milkyway techfile", is_file=True, extra_post_filter_funcs=[self.create_nonempty_check("Milkyway techfile")])

    @property
    def tlu_max_cap_filter(self) -> LibraryFilter:
        """TLU+ max cap filter."""
        def select_tlu_max_cap(lib: hammer_tech.Library) -> List[str]:
            if lib.tluplus_files is not None and lib.tluplus_files.max_cap is not None:
                return [lib.tluplus_files.max_cap]
            else:
                return []
        return LibraryFilter(select_tlu_max_cap, "tlu_max", "TLU+ max cap db", is_file=True)

    @property
    def tlu_min_cap_filter(self) -> LibraryFilter:
        """TLU+ min cap filter."""
        def select_tlu_min_cap(lib: hammer_tech.Library) -> List[str]:
            if lib.tluplus_files is not None and lib.tluplus_files.min_cap is not None:
                return [lib.tluplus_files.min_cap]
            else:
                return []
        return LibraryFilter(select_tlu_min_cap, "tlu_min", "TLU+ min cap db", is_file=True)

    def read_libs(self, libraries: Iterable[LibraryFilter], output_func: Callable[[str, LibraryFilter], List[str]],
                  must_exist: bool = True) -> List[str]:
        """
        Read the given libraries and return a list of strings according to some output format.

        Plan of attack:
        - For every library filter, get a list of lib items
        - Run any extra_post_filter_funcs (if needed)
        - For every lib item in each lib items, run output_func
        - Append everything

        :param libraries: List of libraries to filter, specified as a list of LibraryFilter elements.
        :param output_func: Function which processes the outputs, taking in the filtered lib and the library filter
                            which generated it.
        :param must_exist: Must each library item actually exist? Default: True (yes, they must exist)
        :return: List of filtered libraries processed according output_func.
        """

        def add_lists(a: List[str], b: List[str]) -> List[str]:
            assert isinstance(a, List)
            assert isinstance(b, List)
            return a + b

        def process_library_filter(filt: LibraryFilter) -> List[str]:
            if must_exist:
                existence_check_func = self.make_check_isfile(filt.description) if filt.is_file else self.make_check_isdir(filt.description)
            else:
                existence_check_func = lambda x: x # everything goes

            lib_items = self.filter_and_select_libs(filt.func, extra_funcs=[self.technology.prepend_dir_path,
                                                                            existence_check_func],
                                                    lib_filters=[self.filter_for_supplies])  # type: List[str]
            # Quickly check that lib_items is actually a List[str].
            if not isinstance(lib_items, List):
                raise TypeError("lib_items is not a List[str], but a " + str(type(lib_items)))
            for i in lib_items:
                if not isinstance(i, str):
                    raise TypeError("lib_items is a List but not a List[str]")

            # Apply any list-level functions.
            after_post_filter = reduce_named(
                sequence=filt.extra_post_filter_funcs,
                initial=lib_items,
                function=lambda libs, func: func(list(libs)),
            )

            # Finally, apply any output functions.
            # e.g. turning foo.db into ["--timing", "foo.db"].
            after_output_functions = map(lambda item: output_func(item, filt), after_post_filter)

            # Concatenate lists of List[str] together.
            return list(reduce(add_lists, after_output_functions, []))

        return list(reduce(add_lists, map(process_library_filter, libraries)))

    # TODO: these helper functions might get a bit out of hand, put them somewhere more organized?
    def get_clock_ports(self) -> List[ClockPort]:
        """
        Get the clock ports of the top-level module, as specified in vlsi.inputs.clocks.
        """
        clocks = self.get_setting("vlsi.inputs.clocks")
        output = [] # type: List[ClockPort]
        for clock_port in clocks:
            clock = ClockPort(
                name=clock_port["name"], period=TimeValue(clock_port["period"]),
                uncertainty=None
            )
            if "uncertainty" in clock_port:
                output.append( clock._replace(uncertainty=TimeValue(clock_port["uncertainty"])) )
            else:
                output.append(clock)
        return output

    @staticmethod
    def append_contents_to_path(content_to_append: str, target_path: str) -> None:
        """
        Append the given contents to the file located at target_path, if target_path is not empty.
        :param content_to_append: Content to append.
        :param target_path: Where to append the content.
        """
        if content_to_append != "":
            content_lines = content_to_append.split("\n")  # type: List[str]

            # TODO(edwardw): come up with a more generic "source locator" for hammer
            header_text = "# The following snippet was added by HAMMER"
            content_lines.insert(0, header_text)

            with open(target_path, "a") as f:
                f.write("\n".join(content_lines))

    @staticmethod
    def verbose_tcl_append(cmd: str, output_buffer: List[str]) -> None:
        """
        Helper function to verbosely run a command (print the command before running).
        :param cmd: TCL command to run
        :param output_buffer: Buffer in which to enqueue the resulting TCL lines.
        """
        output_buffer.append("""puts "{0}" """.format(cmd.replace('"', '\"')))
        output_buffer.append(cmd)

class HammerSynthesisTool(HammerTool):
    ### Inputs ###

    @property
    def input_files(self) -> Iterable[str]:
        """
        Get the input collection of source RTL files (e.g. *.v).

        :return: The input collection of source RTL files (e.g. *.v).
        """
        try:
            return self._input_files
        except AttributeError:
            raise ValueError("Nothing set for the input collection of source RTL files (e.g. *.v) yet")

    @input_files.setter
    def input_files(self, value: Iterable[str]) -> None:
        """Set the input collection of source RTL files (e.g. *.v)."""
        if not isinstance(value, Iterable):
            raise TypeError("input_files must be a Iterable[str]")
        self._input_files = value # type: Iterable[str]


    @property
    def top_module(self) -> str:
        """
        Get the top-level module.

        :return: The top-level module.
        """
        try:
            return self._top_module
        except AttributeError:
            raise ValueError("Nothing set for the top-level module yet")

    @top_module.setter
    def top_module(self, value: str) -> None:
        """Set the top-level module."""
        if not isinstance(value, str):
            raise TypeError("top_module must be a str")
        self._top_module = value # type: str


    ### Outputs ###

    @property
    def output_files(self) -> Iterable[str]:
        """
        Get the output collection of mapped (post-synthesis) RTL files.

        :return: The output collection of mapped (post-synthesis) RTL files.
        """
        try:
            return self._output_files
        except AttributeError:
            raise ValueError("Nothing set for the output collection of mapped (post-synthesis) RTL files yet")

    @output_files.setter
    def output_files(self, value: Iterable[str]) -> None:
        """Set the output collection of mapped (post-synthesis) RTL files."""
        if not isinstance(value, Iterable):
            raise TypeError("output_files must be a Iterable[str]")
        self._output_files = value # type: Iterable[str]

class HammerPlaceAndRouteTool(HammerTool):
    ### Generated interface HammerPlaceAndRouteTool ###
    ### Inputs ###

    @property
    def input_files(self) -> Iterable[str]:
        """
        Get the input post-synthesis netlist files.

        :return: The input post-synthesis netlist files.
        """
        try:
            return self._input_files
        except AttributeError:
            raise ValueError("Nothing set for the input post-synthesis netlist files yet")

    @input_files.setter
    def input_files(self, value: Iterable[str]) -> None:
        """Set the input post-synthesis netlist files."""
        if not isinstance(value, Iterable):
            raise TypeError("input_files must be a Iterable[str]")
        self._input_files = value # type: Iterable[str]


    @property
    def top_module(self) -> str:
        """
        Get the top RTL module.

        :return: The top RTL module.
        """
        try:
            return self._top_module
        except AttributeError:
            raise ValueError("Nothing set for the top RTL module yet")

    @top_module.setter
    def top_module(self, value: str) -> None:
        """Set the top RTL module."""
        if not isinstance(value, str):
            raise TypeError("top_module must be a str")
        self._top_module = value # type: str


    ### Outputs ###

# Options for invoking the driver.
HammerDriverOptions = NamedTuple('HammerDriverOptions', [
    # List of environment config files in .json
    ('environment_configs', List[str]),
    # List of project config files in .json
    ('project_configs', List[str]),
    # Log file location.
    ('log_file', str),
    # Folder for storing runtime files / CAD junk.
    ('obj_dir', str)
])


class HammerDriver:
    @staticmethod
    def get_default_driver_options() -> HammerDriverOptions:
        """Get default driver options."""
        return HammerDriverOptions(
            environment_configs=[],
            project_configs=[],
            log_file=datetime.datetime.now().strftime("hammer-vlsi-%Y%m%d-%H%M%S.log"),
            obj_dir=HammerVLSISettings.hammer_vlsi_path
        )

    def __init__(self, options: HammerDriverOptions, extra_project_config: dict = {}) -> None:
        """
        Create a hammer-vlsi driver, which is a higher level convenience function
        for quickly using hammer-vlsi. It imports and uses the hammer-vlsi blocks.

        Set up logging, databases, context, etc.

        :param options: Driver options.
        :param extra_project_config: An extra flattened config for the project. Optional.
        """

        # Create global logging context.
        file_logger = HammerVLSIFileLogger(options.log_file)
        HammerVLSILogging.add_callback(file_logger.callback)
        self.log = HammerVLSILogging.context() # type: HammerVLSILoggingContext

        # Create a new hammer database.
        self.database = hammer_config.HammerDatabase() # type: hammer_config.HammerDatabase

        self.log.info("Loading hammer-vlsi libraries and reading settings")

        # Store the run dir.
        self.obj_dir = options.obj_dir # type: str

        # Load in builtins.
        self.database.update_builtins([
            hammer_config.load_config_from_file(os.path.join(HammerVLSISettings.hammer_vlsi_path, "builtins.yml"), strict=True),
            HammerVLSISettings.get_config()
        ])

        # Read in core defaults.
        self.database.update_core(hammer_config.load_config_from_defaults(HammerVLSISettings.hammer_vlsi_path))

        # Read in the environment config for paths to CAD tools, etc.
        for config in options.environment_configs:
            if not os.path.exists(config):
                self.log.error("Environment config %s does not exist!" % (config))
        self.database.update_environment(hammer_config.load_config_from_paths(options.environment_configs, strict=True))

        # Read in the project config to find the syn, par, and tech.
        project_configs = hammer_config.load_config_from_paths(options.project_configs, strict=True)
        project_configs.append(extra_project_config)
        self.database.update_project(project_configs)
        # Store input config for later.
        self.project_config = hammer_config.combine_configs(project_configs) # type: dict

        # Get the technology and load technology settings.
        self.tech = None # type: hammer_tech.HammerTechnology
        self.load_technology()

        # Keep track of what the synthesis and par configs are since
        # update_tools() just takes a whole list.
        self.tool_configs = {} # type: Dict[str, List[dict]]

        # Initialize tool fields.
        self.syn_tool = None # type: HammerSynthesisTool
        self.par_tool = None # type: HammerPlaceAndRouteTool

    def load_technology(self, cache_dir: str = "") -> None:
        tech_str = self.database.get_setting("vlsi.core.technology")

        if cache_dir == "":
            cache_dir = os.path.join(self.obj_dir, "tech-%s-cache" % tech_str)

        tech_paths = self.database.get_setting("vlsi.core.technology_path")
        tech_json_path = "" # type: str
        for path in tech_paths:
            tech_json_path = os.path.join(path, tech_str, "%s.tech.json" % tech_str)
            if os.path.exists(tech_json_path):
                break
        if tech_json_path == "":
            self.log.error("Technology {0} not found or missing .tech.json!".format(tech_str))
            return
        self.log.info("Loading technology '{0}'".format(tech_str))
        self.tech = hammer_tech.HammerTechnology.load_from_dir(tech_str, os.path.dirname(tech_json_path))  # type: hammer_tech.HammerTechnology
        self.tech.logger = self.log.context("tech")
        self.tech.set_database(self.database)
        self.tech.cache_dir = cache_dir
        self.tech.extract_technology_files()
        self.database.update_technology(self.tech.get_config())


    def update_tool_configs(self) -> None:
        """
        Calls self.database.update_tools with self.tool_configs as a list.
        """
        tools = reduce(lambda a, b: a + b, list(self.tool_configs.values()))
        self.database.update_tools(tools)

    def load_par_tool(self, run_dir: str = "") -> bool:
        """
        Load the place and route tool based on the given database.

        :param run_dir: Directory to use for the tool run_dir. Defaults to the run_dir passed in the HammerDriver
        constructor.
        """
        if run_dir == "":
            run_dir = os.path.join(self.obj_dir, "par-rundir")

        par_tool_name = self.database.get_setting("vlsi.core.par_tool")
        par_tool_get = load_tool(
            path=self.database.get_setting("vlsi.core.par_tool_path"),
            tool_name=par_tool_name
        )
        assert isinstance(par_tool_get, HammerPlaceAndRouteTool), "Par tool must be a HammerPlaceAndRouteTool"
        par_tool = par_tool_get # type: HammerPlaceAndRouteTool
        par_tool.name = par_tool_name
        par_tool.logger = self.log.context("par")
        par_tool.technology = self.tech
        par_tool.set_database(self.database)
        par_tool.run_dir = run_dir

        # TODO: automate this based on the definitions
        par_tool.input_files = self.database.get_setting("par.inputs.input_files")
        par_tool.top_module = self.database.get_setting("par.inputs.top_module")

        self.par_tool = par_tool

        self.tool_configs["par"] = par_tool.get_config()
        self.update_tool_configs()
        return True

    def load_synthesis_tool(self, run_dir: str = "") -> bool:
        """
        Load the synthesis tool based on the given database.

        :param run_dir: Directory to use for the tool run_dir. Defaults to the run_dir passed in the HammerDriver
        constructor.
        :return: True if synthesis tool loading was successful, False otherwise.
        """
        if run_dir == "":
            run_dir = os.path.join(self.obj_dir, "syn-rundir")

        # Find the synthesis/par tool and read in their configs.
        syn_tool_name = self.database.get_setting("vlsi.core.synthesis_tool")
        syn_tool_get = load_tool(
            path=self.database.get_setting("vlsi.core.synthesis_tool_path"),
            tool_name=syn_tool_name
        )
        if not isinstance(syn_tool_get, HammerSynthesisTool):
            self.log.error("Synthesis tool must be a HammerSynthesisTool")
            return False
        # TODO: generate this automatically
        syn_tool = syn_tool_get # type: HammerSynthesisTool
        syn_tool.name = syn_tool_name
        syn_tool.logger = self.log.context("synthesis")
        syn_tool.technology = self.tech
        syn_tool.set_database(self.database)
        syn_tool.run_dir = run_dir

        syn_tool.input_files = self.database.get_setting("synthesis.inputs.input_files")
        syn_tool.top_module = self.database.get_setting("synthesis.inputs.top_module", nullvalue="")
        missing_inputs = False
        if syn_tool.top_module == "":
            self.log.error("Top module not specified for synthesis")
            missing_inputs = True
        if len(syn_tool.input_files) == 0:
            self.log.error("No input files specified for synthesis")
            missing_inputs = True
        if missing_inputs:
            return False

        self.syn_tool = syn_tool

        self.tool_configs["synthesis"] = syn_tool.get_config()
        self.update_tool_configs()
        return True

    def run_synthesis(self) -> dict:
        """
        Run synthesis based on the given database.
        """

        # TODO: think about artifact storage?
        self.log.info("Starting synthesis with tool '%s'" % (self.syn_tool.name))
        if not self.syn_tool.run():
            self.log.error("Synthesis tool %s failed! Please check its output." % (self.syn_tool.name))
            # Allow the flow to keep running, just in case.
            # TODO: make this an option

        # Record output from the syn_tool into the JSON output.
        output_config = dict(self.project_config)
        # TODO(edwardw): automate this
        try:
            output_config["synthesis.outputs.output_files"] = self.syn_tool.output_files
            output_config["synthesis.inputs.input_files"] = self.syn_tool.input_files
            output_config["synthesis.inputs.top_module"] = self.syn_tool.top_module
        except ValueError as e:
            self.log.fatal(e.args[0])
            return {}

        return output_config

    @staticmethod
    def generate_par_inputs_from_synthesis(config_in: dict) -> dict:
        """Generate the appropriate inputs for running place-and-route from the outputs of synthesis run."""
        output_dict = dict(config_in)
        # Plug in the outputs of synthesis into the par inputs.
        output_dict["par.inputs.input_files"] = output_dict["synthesis.outputs.output_files"]
        output_dict["par.inputs.top_module"] = output_dict["synthesis.inputs.top_module"]
        return output_dict

    def run_par(self) -> dict:
        """
        Run place and route based on the given database.
        """
        self.log.info("Starting place and route with tool '%s'" % (self.par_tool.name))
        # TODO: get place and route working
        self.par_tool.run()
        return {}

class HasSDCSupport(HammerTool):
    """Mix-in trait with functions useful for tools with SDC-style
    constraints."""
    @property
    def sdc_clock_constraints(self) -> str:
        """Generate TCL fragments for top module clock constraints."""
        output = [] # type: List[str]

        clocks = self.get_clock_ports()
        for clock in clocks:
            # TODO: FIXME This assumes that library units are always in ns!!!
            output.append("create_clock {0} -name {0} -period {1}".format(clock.name, clock.period.value_in_units("ns")))
            if clock.uncertainty is not None:
                output.append("set_clock_uncertainty {1} [get_clocks {0}]".format(clock.name, clock.uncertainty.value_in_units("ns")))

        output.append("\n")
        return "\n".join(output)

class CadenceTool(HasSDCSupport, HammerTool):
    """Mix-in trait with functions useful for Cadence-based tools."""
    @property
    def env_vars(self) -> Dict[str, str]:
        """
        Get the list of environment variables required for this tool.
        Note to subclasses: remember to include variables from super().env_vars!
        """
        return {
            "CDS_LIC_FILE": self.get_setting("cadence.CDS_LIC_FILE"),
            "CADENCE_HOME": self.get_setting("cadence.cadence_home")
        }

    def get_liberty_libs(self) -> str:
        """
        Helper function to get the list of ASCII liberty files in space separated format.
        :return: List of lib files separated by spaces
        """
        lib_args = self.read_libs([
            self.liberty_lib_filter
        ], self.to_plain_item)
        return " ".join(lib_args)

class SynopsysTool(HasSDCSupport, HammerTool):
    """Mix-in trait with functions useful for Synopsys-based tools."""
    @property
    def env_vars(self) -> Dict[str, str]:
        """
        Get the list of environment variables required for this tool.
        Note to subclasses: remember to include variables from super().env_vars!
        """
        return {
            "SNPSLMD_LICENSE_FILE": self.get_setting("synopsys.SNPSLMD_LICENSE_FILE"),
            # TODO: this is actually a Mentor Graphics licence, not sure why the old dc scripts depend on it.
            "MGLS_LICENSE_FILE": self.get_setting("synopsys.MGLS_LICENSE_FILE")
        }

    def get_synopsys_rm_tarball(self, product: str, settings_key: str = "") -> str:
        """Locate reference methodology tarball.

        :param product: Either "DC" or "ICC"
        :param settings_key: Key to retrieve the version for the product. Leave blank for DC and ICC.
        """
        key = settings_key # type: str
        if product == "DC":
            key = "synthesis.dc.dc_version"
        elif product == "ICC":
            key = "par.icc.icc_version"

        synopsys_rm_tarball = os.path.join(self.get_setting("synopsys.rm_dir"), "%s-RM_%s.tar" % (product, self.get_setting(key)))
        if not os.path.exists(synopsys_rm_tarball):
            # TODO: convert these to logger calls
            raise FileNotFoundError("Expected reference methodology tarball not found at %s. Use the Synopsys RM generator <https://solvnet.synopsys.com/rmgen> to generate a DC reference methodology. If these tarballs have been pre-downloaded, you can set synopsys.rm_dir instead of generating them yourself." % (synopsys_rm_tarball))
        else:
            return synopsys_rm_tarball

def load_tool(tool_name: str, path: Iterable[str]) -> HammerTool:
    """
    Load the given tool.
    See the hammer-vlsi README for how it works.

    :param tool_name: Name of the tool
    :param path: List of paths to get
    :return: HammerTool of the given tool
    """
    # Temporarily add to the import path.
    for p in path:
        sys.path.insert(0, p)
    try:
        mod = importlib.import_module(tool_name)
    except ImportError:
        raise ValueError("No such tool " + tool_name)
    # Now restore the original import path.
    for _ in path:
        sys.path.pop(0)
    try:
        htool = getattr(mod, "tool")
    except AttributeError:
        raise ValueError("No such tool " + tool_name + ", or tool does not follow the hammer-vlsi tool library format")

    if not isinstance(htool, HammerTool):
        raise ValueError("Tool must be a HammerTool")

    # Set the tool directory.
    htool.tool_dir = os.path.dirname(os.path.abspath(mod.__file__))
    return htool


def reduce_named(function: Callable, sequence: Iterable, initial=None) -> Any:
    """
    Version of functools.reduce with named arguments.
    See https://mail.python.org/pipermail/python-ideas/2014-October/029803.html
    """
    if initial is None:
        return reduce(function, sequence)
    else:
        return reduce(function, sequence, initial)