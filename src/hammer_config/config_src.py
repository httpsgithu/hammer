#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
#  Build the configuration database from a series of JSON config files.
#  Dumps the output in JSON format to standard output.
#  See README.config for more details.
#
#  See LICENSE for licence details.

# pylint: disable=invalid-name

from typing import Iterable, List, Union, Callable, Any, Dict, Set, NamedTuple, Tuple, Optional

from hammer_utils import deepdict, topological_sort
from .yaml2json import load_yaml  # grumble grumble

from functools import reduce, lru_cache
import json
import numbers
import os
import re

# Special key used for meta directives which require config paths like prependlocal.
_CONFIG_PATH_KEY = "_config_path"

# Special key used to keep track of the next available integer suffix to avoid
# duplicate keys.
_NEXT_FREE_INDEX_KEY = "_next_free_index"


def _get_next_free_index(d: dict) -> int:
    """
    Get the next free index in the given dictionary.
    Side effect: increments the next free index stored in the dictionary by 1.
    If the key does not exist, create it and set it to 2, and return 1.
    :param d: Dictionary to find the next free index in.
    :return: Next free index.
    """
    if _NEXT_FREE_INDEX_KEY not in d:
        d[_NEXT_FREE_INDEX_KEY] = 1
    next_index = int(d[_NEXT_FREE_INDEX_KEY])
    d[_NEXT_FREE_INDEX_KEY] = next_index + 1
    return next_index


# Miscellaneous parameters involved in executing a meta directive.
class MetaDirectiveParams(NamedTuple('MetaDirectiveParams', [
    # Path of the config that contained the meta directive.
    # Used mainly for prependlocal.
    ('meta_path', str)
])):
    __slots__ = ()


# Represents a meta directive in the Hammer configuration system.
class MetaDirective(NamedTuple('MetaDirective', [
    # Action which executes/implements this meta directive.
    # config_dict is the base dictionary
    # key is the key of the meta directive
    # value is the value of that key
    # params contains miscellaneous parameters required to execute meta directives.
    # def action(config_dict: dict, key: str, value: Any, params: MetaDirectiveParams) -> None:
    #     ...
    ('action', Callable[[dict, str, Any, MetaDirectiveParams], None]),
    # Function which takes in the key and value for a meta directive and
    # returns a list of settings it depends on.
    # e.g. for subst, a value of "${a}${b}" would return
    # ['a', 'b'].
    # def target_settings(key: str, value: Any) -> List[str]:
    #     ...
    ('target_settings', Callable[[str, Any], List[str]]),
    # Function which takes in the key and value for a meta directive and
    # changes its value so that any references to a particular target key
    # is changed to another.
    # It turns a tuple of (new value, new meta type).
    # The target_key must be one of the keys in target_settings.
    # Returns None if the target_key was not found or could not be replaced.
    # def rename_target(key: str, value: Any, target_setting: str, replacement_setting: str) -> Optional[Tuple[Any, str]]:
    #     ...
    ('rename_target', Callable[[str, Any, str, str], Optional[Tuple[Any, str]]])
])):
    __slots__ = ()


@lru_cache(maxsize=2)
def get_meta_directives() -> Dict[str, MetaDirective]:
    """
    Get all meta directives available.
    :return: Meta directives indexed by action (e.g. "subst").
    """
    directives = {}  # type: Dict[str, MetaDirective]

    # Helper functions to implement each meta directive.
    def append_action(config_dict: dict, key: str, value: Any, params: MetaDirectiveParams) -> None:
        if key not in config_dict:
            config_dict[key] = []

        if not isinstance(config_dict[key], list):
            raise ValueError("Trying to append to non-list setting %s" % (key))
        if not isinstance(value, list):
            raise ValueError("Trying to append to list %s with non-list %s" % (key, str(value)))
        config_dict[key] += value

    def append_rename(key: str, value: Any, target_setting: str, replacement_setting: str) -> Optional[Tuple[Any, str]]:
        return [replacement_setting, value], "crossappend"

    # append depends only on itself
    directives['append'] = MetaDirective(action=append_action,
                                         target_settings=lambda key, value: [key],
                                         rename_target=append_rename)

    def crossappend_decode(value: Any) -> Tuple[str, list]:
        assert isinstance(value, list), "crossappend takes a list of two elements"
        assert len(value) == 2, "crossappend takes a list of two elements"
        target_setting = value[0]  # type: str
        append_value = value[1]  # type: list
        assert isinstance(target_setting, str), "crossappend target setting must be a string"
        assert isinstance(append_value, list), "crossappend must append a list"
        return target_setting, append_value

    # crossappend takes a list that has two elements.
    # The first is the target list (the list to append to), and the second is
    # a list to append to the target list.
    # e.g. if base has ["1"] and crossappend has ["base", ["2", "3"]], then
    # the result will be ["1", "2", "3"].
    def crossappend_action(config_dict: dict, key: str, value: Any, params: MetaDirectiveParams) -> None:
        target_setting, append_value = crossappend_decode(value)
        config_dict[key] = config_dict[target_setting] + append_value

    def crossappend_targets(key: str, value: Any) -> List[str]:
        target_setting, append_value = crossappend_decode(value)
        return [target_setting]

    def crossappend_rename(key: str, value: Any, target_setting: str, replacement_setting: str) -> Optional[
        Tuple[Any, str]]:
        crossappend_target, append_value = crossappend_decode(value)
        return [replacement_setting if crossappend_target == target_setting else crossappend_target,
                append_value], "crossappend"

    directives['crossappend'] = MetaDirective(action=crossappend_action,
                                              target_settings=crossappend_targets,
                                              rename_target=crossappend_rename)

    def crossappendref_decode(value: Any) -> Tuple[str, str]:
        assert isinstance(value, list), "crossappendref takes a list of two elements"
        assert len(value) == 2, "crossappendref takes a list of two elements"
        target_key = value[0]  # type: str
        append_key = value[1]  # type: str
        assert isinstance(target_key, str), "crossappendref target setting must be a string"
        assert isinstance(append_key, str), "crossappend append list setting must be a string"
        return target_key, append_key

    # crossappendref takes a list that has two elements.
    # The first is the target list (the list to append to), and the second is
    # a setting that contains a list to append.
    # e.g. if base has ["1"], app has ["2", "3"], and crossappend has ["base", "app"], the result
    # is ["1", "2", "3"].
    def crossappendref_action(config_dict: dict, key: str, value: Any, params: MetaDirectiveParams) -> None:
        target_setting, append_setting = crossappendref_decode(value)
        config_dict[key] = config_dict[target_setting] + config_dict[append_setting]

    def crossappendref_targets(key: str, value: Any) -> List[str]:
        target_setting, append_setting = crossappendref_decode(value)
        return [target_setting, append_setting]

    def crossappendref_rename(key: str, value: Any, target_setting: str, replacement_setting: str) -> Optional[
        Tuple[Any, str]]:
        target, append = crossappendref_decode(value)

        def replace_if_target_setting(setting: str) -> str:
            """Helper function to replace the given setting with the
            replacement if it is equal to target_setting."""
            return replacement_setting if setting == target_setting else setting

        return [replace_if_target_setting(target),
                replace_if_target_setting(append)], "crossappendref"

    directives['crossappendref'] = MetaDirective(action=crossappendref_action,
                                                 target_settings=crossappendref_targets,
                                                 rename_target=crossappendref_rename)

    def subst_str(input_str: str, replacement_func: Callable[[str], str]) -> str:
        """Substitute ${...}"""
        return re.sub(__VARIABLE_EXPANSION_REGEX, lambda x: replacement_func(x.group(1)), input_str)

    def subst_action(config_dict: dict, key: str, value: Any, params: MetaDirectiveParams) -> None:
        def perform_subst(value: Union[str, List[str]]) -> Union[str, List[str]]:
            """
            Perform substitutions for the given value.
            If value is a string, perform substitutions in the string. If value is a list, then perform substitutions
            in every string in the list.
            :param value: String or list
            :return: String or list but with everything substituted.
            """
            newval = ""  # type: Union[str, List[str]]

            if isinstance(value, list):
                newval = list(map(lambda input_str: subst_str(input_str, lambda key: config_dict[key]), value))
            else:
                newval = subst_str(value, lambda key: config_dict[key])
            return newval

        config_dict[key] = perform_subst(value)

    def subst_targets(key: str, value: Any) -> List[str]:
        assert isinstance(value, str)

        output_vars = []  # type: List[str]

        matches = re.finditer(__VARIABLE_EXPANSION_REGEX, value, re.DOTALL)
        for match in matches:
            output_vars.append(match.group(1))

        return output_vars

    def subst_rename(key: str, value: Any, target_setting: str, replacement_setting: str) -> Optional[Tuple[Any, str]]:
        assert isinstance(value, str)

        if target_setting not in subst_targets(key, value):
            return None

        new_value = subst_str(value, lambda key: "${" + replacement_setting + "}" if key == target_setting else key)
        return new_value, "subst"

    directives['subst'] = MetaDirective(action=subst_action,
                                        target_settings=subst_targets,
                                        rename_target=subst_rename)

    def crossref_check_and_cast(k: Any) -> str:
        if not isinstance(k, str):
            raise ValueError("crossref (if used with lists) can only be used only with lists of strings")
        else:
            return k

    def crossref_action(config_dict: dict, key: str, value: Any, params: MetaDirectiveParams) -> None:
        """
        Copy the contents of the referenced key for use as this key's value.
        If the reference is a list, then apply the crossref for each element
        of the list.
        """
        if type(value) == str:
            config_dict[key] = config_dict[value]
        elif type(value) == list:
            def check_and_get(k: Any) -> Any:
                return config_dict[crossref_check_and_cast(k)]

            config_dict[key] = list(map(check_and_get, value))
        elif isinstance(value, numbers.Number):
            # bools are instances of numbers.Number for some weird reason
            raise ValueError("crossref cannot be used with numbers and bools")
        else:
            raise NotImplementedError("crossref not implemented on other types yet")

    def crossref_targets(key: str, value: Any) -> List[str]:
        if type(value) == str:
            return [value]
        elif type(value) == list:
            return list(map(crossref_check_and_cast, value))
        elif isinstance(value, numbers.Number):
            # bools are instances of numbers.Number for some weird reason
            raise ValueError("crossref cannot be used with numbers and bools")
        else:
            raise NotImplementedError("crossref not implemented on other types yet")

    def crossref_rename(key: str, value: Any, target_setting: str, replacement_setting: str) -> Optional[
        Tuple[Any, str]]:
        def change_if_target(x: str) -> str:
            if x == target_setting:
                return replacement_setting
            else:
                return x

        if type(value) == str:
            return [change_if_target(value)], "crossref"
        elif type(value) == list:
            return list(map(change_if_target, map(crossref_check_and_cast, value))), "crossref"
        elif isinstance(value, numbers.Number):
            # bools are instances of numbers.Number for some weird reason
            raise ValueError("crossref cannot be used with numbers and bools")
        else:
            raise NotImplementedError("crossref not implemented on other types yet")

    directives['crossref'] = MetaDirective(action=crossref_action,
                                           target_settings=crossref_targets,
                                           rename_target=crossref_rename)

    def transclude_action(config_dict: dict, key: str, value: Any, params: MetaDirectiveParams) -> None:
        """Transclude the contents of the file pointed to by value."""
        assert isinstance(value, str), "Path to file for transclusion must be a string"
        with open(value, "r") as f:
            file_contents = str(f.read())
        config_dict[key] = file_contents

    def transclude_rename(key: str, value: Any, target_setting: str, replacement_setting: str) -> Optional[
        Tuple[Any, str]]:
        # This meta directive doesn't depend on any settings
        return value, "transclude"

    # transclude depends on external files, not other settings.
    directives['transclude'] = MetaDirective(action=transclude_action,
                                             target_settings=lambda key, value: [],
                                             rename_target=transclude_rename)

    def json2list_action(config_dict: dict, key: str, value: Any, params: MetaDirectiveParams) -> None:
        """Turn the value of the key (JSON list) into a list."""
        assert isinstance(value, str), "json2list requires a JSON string that is a list"
        parsed = json.loads(value)
        assert isinstance(parsed, list), "json2list requires a JSON string that is a list"
        config_dict[key] = parsed

    def json2list_rename(key: str, value: Any, target_setting: str, replacement_setting: str) -> Optional[
        Tuple[Any, str]]:
        # This meta directive doesn't depend on any settings
        return value, "json2list"

    # json2list does not depend on anything
    directives['json2list'] = MetaDirective(action=json2list_action,
                                            target_settings=lambda key, value: [],
                                            rename_target=json2list_rename)

    def prependlocal_action(config_dict: dict, key: str, value, params: MetaDirectiveParams) -> None:
        """Prepend the local path of the config dict."""
        config_dict[key] = os.path.join(params.meta_path, str(value))

    def prependlocal_rename(key: str, value: Any, target_setting: str, replacement_setting: str) -> Optional[
        Tuple[Any, str]]:
        # This meta directive doesn't depend on any settings
        return value, "prependlocal"

    # prependlocal does not depend on anything in config_dict.
    directives['prependlocal'] = MetaDirective(action=prependlocal_action,
                                               target_settings=lambda key, value: [],
                                               rename_target=prependlocal_rename)

    return directives


def unpack(config_dict: dict, prefix: str = "") -> dict:
    """
    Unpack the given config_dict, flattening key names recursively.
    >>> p = unpack({"one": 1, "two": 2}, prefix="snack")
    >>> p == {'snack.one': 1, 'snack.two': 2}
    True
    >>> p = unpack({"a": {"foo": 1, "bar": 2}})
    >>> p == {'a.foo': 1, 'a.bar': 2}
    True
    >>> p = unpack({"a.b": {"foo": 1, "bar": 2}})
    >>> p == {"a.b.foo": 1, "a.b.bar": 2}
    True
    >>> p = unpack({
    ...     "a": {
    ...         "foo": 1,
    ...         "bar": 2
    ...     },
    ...     "b": {
    ...         "baz": 3,
    ...         "boom": {"rocket": "chip", "hwacha": "vector"}
    ...     },
    ... })
    >>> p == {"a.foo": 1, "a.bar": 2, "b.baz": 3, "b.boom.rocket": "chip",
    ...     "b.boom.hwacha": "vector"}
    True
    """
    # We don't want an extra "." in the beginning.
    real_prefix = "" if prefix == "" else prefix + "."
    output_dict = {}
    for key, value in config_dict.items():
        if isinstance(value, dict):
            output_dict.update(unpack(value, real_prefix + key))
        else:
            output_dict[real_prefix + key] = value
    return output_dict


def reverse_unpack(input_dict: dict) -> dict:
    """
    Reverse the effects of unpack(). Mainly useful for testing purposes.
    >>> p = reverse_unpack({"a.b": 1})
    >>> p == {"a": {"b": 1}}
    True
    :param input: Unpacked input_dict dictionary
    :return: Packed equivalent of input_dict
    """
    output_dict = {}  # type: Dict[str, Any]

    def get_subdict(parts: List[str], current_root: dict) -> dict:
        if len(parts) == 0:
            return current_root
        else:
            if parts[0] not in current_root:
                current_root[parts[0]] = {}
            return get_subdict(parts[1:], current_root[parts[0]])

    for key, value in input_dict.items():
        key_parts = key.split(".")
        if len(key_parts) >= 1:
            containing_dict = get_subdict(key_parts[:-1], output_dict)
        else:
            assert False, "Cannot have blank key"
        containing_dict[key_parts[-1]] = value
    return output_dict


__VARIABLE_EXPANSION_REGEX = r'\${([a-zA-Z_\-\d.]+)}'


def update_and_expand_meta(config_dict: dict, meta_dict: dict) -> dict:
    """
    Expand the meta directives for the given config dict and return a new
    dictionary containing the updated settings with respect to the base config_dict.

    :param config_dict: Base config.
    :param meta_dict: Dictionary with potentially new meta directives.
    :return: New dictionary with meta_dict updating config_dict.
    """
    assert isinstance(config_dict, dict)
    assert isinstance(meta_dict, dict)

    newdict = deepdict(config_dict)

    # Find meta directives.
    meta_dict = deepdict(meta_dict)  # create a copy so we can remove items.
    meta_dict_keys = list(meta_dict.keys())
    meta_keys = filter(lambda k: k.endswith("_meta"), meta_dict_keys)

    # Deal with meta directives.
    meta_len = len("_meta")
    for meta_key in meta_keys:
        setting = meta_key[:-meta_len]
        meta_type_from_dict = meta_dict[meta_key]  # type: Union[str, List[str]]
        meta_directives = []  # type: List[str]
        if isinstance(meta_type_from_dict, str):
            meta_directives = [meta_type_from_dict]
        else:
            if not isinstance(meta_type_from_dict, list):
                raise ValueError("A meta directive must either be a string or a list of strings")
            meta_directives = meta_type_from_dict

        # Process each meta type in order.
        seen_lazy = False  # type: bool
        for meta_type in meta_directives:
            if not isinstance(meta_type, str):
                raise TypeError("meta_type was not a string: " + repr(meta_type))

            # If it's a lazy meta, skip it for now since they are lazily
            # processed at the very end.
            if meta_type.startswith("dynamic"):
                raise ValueError(
                    "Found meta type {meta_type}. "
                    "Dynamic meta directives were renamed to lazy meta directives after issue #134. "
                    "Please change your metas from dynamic* to lazy*".format(
                        meta_type=meta_type))
            if meta_type.startswith("lazy"):
                lazy_base_meta_type = meta_type[len("lazy"):]

                if lazy_base_meta_type not in get_meta_directives():
                    raise ValueError("The type of lazy meta variable %s is not supported (%s)" % (meta_key, meta_type))

                if seen_lazy:
                    raise ValueError("Multiple lazy directives in a single directive array not supported yet")
                else:
                    seen_lazy = True

                update_dict = {}  # type: dict

                # Check if this lazy meta references itself by checking if any of its targets is itself.
                targets = get_meta_directives()[lazy_base_meta_type].target_settings(setting, meta_dict[setting])
                if len(list(filter(lambda x: x == setting, targets))) > 0:
                    # If it does, rename this lazy meta to reference a new base.
                    # e.g. if a (dict 2) -> a (dict 1), rename "a (dict 1)" to a_1.
                    next_index = _get_next_free_index(newdict)
                    new_base_setting = "{setting}_{index}".format(
                        setting=setting,
                        index=next_index)
                    new_value_meta = get_meta_directives()[lazy_base_meta_type].rename_target(setting,
                                                                                              meta_dict[setting],
                                                                                              setting,
                                                                                              new_base_setting)  # type: Optional[Tuple[Any, str]]
                    if new_value_meta is None:
                        raise ValueError(
                            "Failed to rename lazy setting which depends on itself ({})".format(setting))
                    else:
                        new_value, new_meta = new_value_meta

                    # Rename base setting to new_base_setting, and add the new setting.
                    update_dict.update({
                        new_base_setting: newdict[setting],
                        setting: new_value,
                        setting + "_meta": "lazy" + new_meta  # these are lazy metas
                    })
                    if setting + "_meta" in newdict:
                        update_dict.update({
                            new_base_setting + "_meta": newdict[setting + "_meta"]
                        })
                else:
                    # Store it into newdict and skip processing now.
                    update_dict.update({
                        setting: meta_dict[setting],
                        setting + "_meta": meta_type
                    })
                newdict.update(update_dict)
                continue
            else:
                if seen_lazy:
                    raise ValueError("Cannot use a non-lazy meta directive after a lazy one")

            try:
                meta_func = get_meta_directives()[meta_type].action
            except KeyError:
                raise ValueError("The type of meta variable %s is not supported (%s)" % (meta_key, meta_type))
            meta_func(newdict, setting, meta_dict[setting],
                      MetaDirectiveParams(meta_path=meta_dict.get(_CONFIG_PATH_KEY, "unspecified")))
            # Update meta_dict if there are multiple meta directives.
            meta_dict[setting] = newdict[setting]

        del meta_dict[meta_key]
        del meta_dict[setting]

    newdict.update(deepdict(meta_dict))  # Update everything else.
    return newdict


class HammerDatabase:
    """
    Define a database which is composed of a set of overridable configs.
    We need something like this in order to e.g. bind technology afterwards, since we never want technology to override project.
    If we just did an .update() with the technology config, we'd possibly lose the previously-bound project config.

    Terminology:
    - setting: a single key-value pair e.g. "vlsi.core.technology" -> "footech"
    - config: a single concrete dictionary of settings.
    - database: a collection of configs with a specific override hierarchy.

    Order of precedence (in increasing order):
    - builtins
    - core
    - tools
    - technology
    - environment
    - project
    - runtime (settings lazyally updated during the run a hammer run)
    """

    def __init__(self) -> None:
        self.builtins = []  # type: List[dict]
        self.core = []  # type: List[dict]
        self.tools = []  # type: List[dict]
        self.technology = []  # type: List[dict]
        self.environment = []  # type: List[dict]
        self.project = []  # type: List[dict]
        self._runtime = {}  # type: Dict[str, Any]

        self.__config_cache = {}  # type: dict
        self.__config_cache_dirty = False  # type: bool

    @property
    def runtime(self) -> List[dict]:
        return [self._runtime]

    @staticmethod
    def internal_keys() -> Set[str]:
        """Internal keys that shouldn't show up in any final config."""
        return {_CONFIG_PATH_KEY, _NEXT_FREE_INDEX_KEY}

    def get_config(self) -> dict:
        """
        Get the config of this database after all the overrides have been dealt with.
        """
        if self.__config_cache_dirty:
            self.__config_cache = combine_configs(
                [{}] + self.builtins + self.core + self.tools + self.technology + self.environment +
                self.project + self.runtime)
            self.__config_cache_dirty = False
        return self.__config_cache

    def get_database_json(self) -> str:
        """Get the database (get_config) in JSON form as a string.
        """
        return json.dumps(self.get_config(), sort_keys=True, indent=4, separators=(',', ': '))

    def get(self, key: str) -> Any:
        """Alias for get_setting()."""
        return self.get_setting(key)

    def __getitem__(self, key: str) -> Any:
        """Alias for get_setting()."""
        return self.get_setting(key)

    def __contains__(self, item: str) -> bool:
        """Alias for has_setting()."""
        return self.has_setting(item)

    def get_setting(self, key: str, nullvalue: Any = None) -> Any:
        """
        Retrieve the given key.

        :param key: Desired key.
        :param nullvalue: Value to return out for nulls.
        :return: The given config
        """
        if key not in self.get_config():
            raise KeyError("Key " + key + " is missing")
        else:
            value = self.get_config()[key]
            return nullvalue if value is None else value

    def set_setting(self, key: str, value: Any) -> None:
        """
        Set the given key. The setting will be placed into the runtime dictionary.

        :param key: Key
        :param value: Value for key
        """
        self._runtime[key] = value
        self.__config_cache_dirty = True

    def has_setting(self, key: str) -> bool:
        """
        Check if the given key exists in the database.

        :param key: Desired key.
        :return: True if the given setting exists.
        """
        return key in self.get_config()

    def update_core(self, core_config: List[dict]) -> None:
        """
        Update the core config with the given core config.
        """
        self.core = core_config
        self.__config_cache_dirty = True

    def update_tools(self, tools_config: List[dict]) -> None:
        """
        Update the tools config with the given tools config.
        """
        self.tools = tools_config
        self.__config_cache_dirty = True

    def update_technology(self, technology_config: List[dict]) -> None:
        """
        Update the technology config with the given technology config.
        """
        self.technology = technology_config
        self.__config_cache_dirty = True

    def update_environment(self, environment_config: List[dict]) -> None:
        """
        Update the environment config with the given environment config.
        """
        self.environment = environment_config
        self.__config_cache_dirty = True

    def update_project(self, project_config: List[dict]) -> None:
        """
        Update the project config with the given project config.
        """
        self.project = project_config
        self.__config_cache_dirty = True

    def update_builtins(self, builtins_config: List[dict]) -> None:
        """
        Update the builtins config with the given builtins config.
        """
        self.builtins = builtins_config
        self.__config_cache_dirty = True


def load_config_from_string(contents: str, is_yaml: bool, path: str = "unspecified") -> dict:
    """
    Load config from a string by loading it and unpacking it.

    :param contents: Contents of the config.
    :param is_yaml: True if the contents are yaml.
    :param path: Path to the folder where the config file is located.
    :return: Loaded config dictionary, unpacked.
    """
    unpacked = unpack(load_yaml(contents) if is_yaml else json.loads(contents))
    unpacked[_CONFIG_PATH_KEY] = path
    return unpacked


def load_config_from_file(filename: str, strict: bool = False) -> dict:
    """
    Load config from a filename, returning a blank dictionary if the file is
    empty, instead of an error.
    Supports .yml and .json, and will raise an error otherwise.

    :param filename: Filename to the config in .yml or .json.
    :param strict: Set to true to error if the file is not found.
    :return: Loaded config dictionary, unpacked.
    """
    if filename.endswith(".yml"):
        is_yaml = True
    elif filename.endswith(".json"):
        is_yaml = False
    else:
        raise ValueError("Invalid config type " + filename)

    try:
        with open(filename, "r") as f:
            file_contents = f.read()
    except FileNotFoundError as e:
        if strict:
            raise e
        else:
            # If the config didn't exist, just return a blank dictionary.
            return {}

    if file_contents.strip() == "":
        return {}
    else:
        return load_config_from_string(file_contents, is_yaml, path=os.path.dirname(filename))


def combine_configs(configs: Iterable[dict]) -> dict:
    """
    Combine the given list of *unpacked* configs into a single config.
    Later configs in the list will override the earlier configs.

    :param configs: List of configs.
    :param handle_meta: Handle meta configs?
    :return: A loaded config dictionary.
    """
    expanded_config_reduce = reduce(update_and_expand_meta, configs, {})  # type: dict
    expanded_config = deepdict(expanded_config_reduce)  # type: dict
    expanded_config_orig = deepdict(expanded_config)  # type: dict

    # Now, we need to handle lazy* metas.
    lazy_metas = {}

    meta_dict_keys = list(expanded_config.keys())
    meta_keys = list(filter(lambda k: k.endswith("_meta"), meta_dict_keys))

    # Graph to keep track of which lazy settings depend on others.
    # key1 -> key2 means key2 depends on key1
    graph = {}  # type: Dict[str, Tuple[List[str], List[str]]]

    meta_len = len("_meta")
    for meta_key in meta_keys:
        setting = meta_key[:-meta_len]  # type: str
        lazy_meta_type = expanded_config[meta_key]  # type: str

        assert lazy_meta_type.startswith("lazy"), "Should have only lazy metas left now"

        # Create lazy_metas without the lazy part.
        # e.g. what used to be a lazysubst just becomes a plain subst since everything is fully resolved now.
        meta_type = lazy_meta_type[len("lazy"):]
        lazy_metas[meta_key] = meta_type
        lazy_metas[setting] = expanded_config[setting]  # copy over the template too

        # Build the graph of which lazy settings depend on what.

        # Always ensure that this lazy setting's node exists even if it has no dependencies.
        if setting not in graph:
            graph[setting] = ([], [])

        for target_var in get_meta_directives()[meta_type].target_settings(setting, expanded_config[setting]):
            # Make sure the order in which we delete doesn't affect this
            # search, since expanded_config might have some deleted stuff.
            if target_var + "_meta" in expanded_config_orig:
                # Add a dependency for target -> this setting
                if target_var not in graph:
                    graph[target_var] = ([], [])
                graph[target_var][0].append(setting)
                graph[setting][1].append(target_var)
            else:
                # The target setting that this depends on is not a lazy setting.
                pass

        # Delete from expanded_config
        del expanded_config[meta_key]
        del expanded_config[setting]

    if len(graph) > 0:
        # Find all the starting nodes (no incoming edges).
        starting_nodes = list(
            map(lambda key_val: key_val[0], filter(lambda key_val: len(key_val[1][1]) == 0, graph.items())))

        # Sort starting nodes for determinism.
        starting_nodes = sorted(starting_nodes)

        if len(starting_nodes) == 0:
            raise ValueError("There appears to be a loop of lazy settings")

        # List of settings to expand first according to topological sort.
        settings_ordered = topological_sort(graph, starting_nodes)  # type: List[str]

        def combine_meta(config_dict: dict, meta_setting: str) -> dict:
            # Merge in the metas in the given order.
            return update_and_expand_meta(config_dict, {
                meta_setting: lazy_metas[meta_setting],
                meta_setting + "_meta": lazy_metas[meta_setting + "_meta"]
            })

        final_dict = reduce(combine_meta, settings_ordered, expanded_config)  # type: dict
    else:
        final_dict = deepdict(expanded_config)

    # Remove any temporary keys.
    for key in HammerDatabase.internal_keys():
        if key in final_dict:
            del final_dict[key]

    return final_dict


def load_config_from_paths(config_paths: Iterable[str], strict: bool = False) -> List[dict]:
    """
    Load configuration from paths containing \*.yml and \*.json files.
    As noted in README.config, .json will take precedence over .yml files.

    :param config_paths: Path to \*.yml and \*.json config files.
    :param strict: Set to true to error if the file is not found.
    :return: A list of configs in increasing order of precedence.
    """
    # Put the .json configs after the .yml configs to make sure .json takes
    # precedence over .yml.
    sorted_paths = sorted(config_paths, key=lambda x: x.endswith(".json"))

    return list(map(lambda path: load_config_from_file(path, strict), sorted_paths))


def load_config_from_defaults(path: str, strict: bool = False) -> List[dict]:
    """
    Load the default configuration for a hammer-vlsi tool/library/technology in
    the given path, which consists of defaults.yml and defaults.json (with
    defaults.json taking priority).

    :param config_paths: Path to defaults.yml and defaults.json.
    :param strict: Set to true to error if the file is not found.
    :return: A list of configs in increasing order of precedence.
    """
    return load_config_from_paths([
        os.path.join(path, "defaults.yml"),
        os.path.join(path, "defaults.json")
    ])
