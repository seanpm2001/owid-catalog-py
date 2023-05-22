#
#  variables.py
#

import json
from os import path
from typing import Any, Dict, List, Optional, Union, cast

import pandas as pd
import structlog
from pandas._typing import Scalar
from pandas.core.series import Series

from .meta import License, Source, VariableMeta
from .properties import metadata_property

log = structlog.get_logger()

SCHEMA = json.load(open(path.join(path.dirname(__file__), "schemas", "table.json")))
METADATA_FIELDS = list(SCHEMA["properties"])

# When creating a new variable, we need to pass a temporary name. For example, when doing tb["a"] + tb["b"]:
#  * If variable.name is None, a ValueError is raised.
#  * If variable.name = self.checked_name then the metadata of the first variable summed ("a") is modified.
#  * If variable.name is always a random string (that does not coincide with an existing variable) then
#    when replacing a variable (e.g. tb["a"] += 1) the original variable loses its metadata.
# For these reasons, we ensure that variable.name is always filled, even with a temporary name.
# In fact, if the new variable becomes a column in a table, its name gets overwritten by the column name (which is a
# nice feature). For example, when doing tb["c"] = tb["a"] + tb["b"], the variable name of "c" will be "c", even if we
# passed a temporary variable name. Therefore, this temporary name may be irrelevant in practice.
# TODO: Is there a better solution for these issues?
UNNAMED_VARIABLE = "**TEMPORARY UNNAMED VARIABLE**"


class Variable(pd.Series):
    _name: Optional[str] = None
    _fields: Dict[str, VariableMeta]

    def __init__(
        self,
        data: Any = None,
        index: Any = None,
        _fields: Optional[Dict[str, VariableMeta]] = None,
        **kwargs: Any,
    ) -> None:
        self._fields = _fields or {}

        # silence warning
        if data is None and not kwargs.get("dtype"):
            kwargs["dtype"] = "object"

        super().__init__(data=data, index=index, **kwargs)

    @property
    def name(self) -> Optional[str]:
        return self._name

    @name.setter
    def name(self, name: str) -> None:
        # None name does not modify _fields, it is usually triggered on pandas operations
        if name is not None:
            # move metadata when you rename a field
            if self._name and self._name in self._fields:
                self._fields[name] = self._fields.pop(self._name)

            # make sure there is always a placeholder metadata object
            if name not in self._fields:
                self._fields[name] = VariableMeta()

        self._name = name

    @property
    def checked_name(self) -> str:
        if not self.name:
            raise ValueError("variable must be named to have metadata")

        return self.name

    # which fields should pandas propagate on slicing, etc?
    _metadata = ["_fields", "_name"]

    @property
    def _constructor(self) -> type:
        return Variable

    @property
    def _constructor_expanddim(self) -> type:
        # XXX lazy circular import
        from . import tables

        return tables.Table

    @property
    def metadata(self) -> VariableMeta:
        return self._fields[self.checked_name]

    @metadata.setter
    def metadata(self, meta: VariableMeta) -> None:
        self._fields[self.checked_name] = meta

    def astype(self, *args: Any, **kwargs: Any) -> "Variable":
        # To fix: https://github.com/owid/owid-catalog-py/issues/12
        v = super().astype(*args, **kwargs)
        v.name = self.name
        return cast(Variable, v)

    # TODO: If I set the type hint of the following functions to -> "Variable" I get typing errors.

    def __add__(self, other: Union[Scalar, Series, "Variable"]) -> Series:
        # variable = super().__add__(other)
        variable = Variable(self.values + other, name=UNNAMED_VARIABLE)  # type: ignore
        variable.metadata = combine_variables_metadata(variables=[self, other], operation="+", name=UNNAMED_VARIABLE)
        return variable

    def __sub__(self, other: Union[Scalar, Series, "Variable"]) -> Series:
        # variable = super().__sub__(other)
        variable = Variable(self.values - other, name=UNNAMED_VARIABLE)  # type: ignore
        variable.metadata = combine_variables_metadata(variables=[self, other], operation="-", name=UNNAMED_VARIABLE)
        return variable

    def __mul__(self, other: Union[Scalar, Series, "Variable"]) -> Series:
        # variable = super().__mul__(other)
        variable = Variable(self.values * other, name=UNNAMED_VARIABLE)  # type: ignore
        variable.metadata = combine_variables_metadata(variables=[self, other], operation="*", name=UNNAMED_VARIABLE)
        return variable

    def __truediv__(self, other: Union[Scalar, Series, "Variable"]) -> Series:
        # variable = super().__truediv__(other)
        variable = Variable(self.values / other, name=UNNAMED_VARIABLE)  # type: ignore
        variable.metadata = combine_variables_metadata(variables=[self, other], operation="/", name=UNNAMED_VARIABLE)
        return variable

    def __floordiv__(self, other: Union[Scalar, Series, "Variable"]) -> Series:
        # variable = super().__floordiv__(other)
        variable = Variable(self.values // other, name=UNNAMED_VARIABLE)  # type: ignore
        variable.metadata = combine_variables_metadata(variables=[self, other], operation="//", name=UNNAMED_VARIABLE)
        return variable

    def __mod__(self, other: Union[Scalar, Series, "Variable"]) -> Series:
        # variable = super().__mod__(other)
        variable = Variable(self.values % other, name=UNNAMED_VARIABLE)  # type: ignore
        variable.metadata = combine_variables_metadata(variables=[self, other], operation="%", name=UNNAMED_VARIABLE)
        return variable

    def __pow__(self, other: Union[Scalar, Series, "Variable"]) -> Series:
        # For some reason, the following line modifies the metadata of the original variable.
        # variable = super().__pow__(other)
        # So, instead, we define a new variable.
        variable = Variable(self.values**other, name=UNNAMED_VARIABLE)  # type: ignore
        variable.metadata = combine_variables_metadata(variables=[self, other], operation="**", name=UNNAMED_VARIABLE)
        return variable

    def fillna(self, value=None, *args, **kwargs) -> Series:
        # variable = super().fillna(value)
        # NOTE: Argument "inplace" will modify the original variable's data, but not its metadata.
        #  But we should not use "inplace" anyway.
        if "inplace" in kwargs and kwargs["inplace"] is True:
            log.warning("Avoid using fillna(inplace=True) may not handle metadata as expected.")
        variable = Variable(super().fillna(value, *args, **kwargs), name=UNNAMED_VARIABLE)  # type: ignore
        variable.metadata = combine_variables_metadata(
            variables=[self, value], operation="fillna", name=UNNAMED_VARIABLE
        )
        return variable

    # TODO: Should we also include the "add", "sub", "mul", "truediv" methods here? For example
    # def add(self, other: Union[Scalar, Series, "Variable"]) -> "Variable":
    #     return self.__add__(other=other)
    # These methods have some additional arguments, namely axis='columns', level=None, fill_value=None that would need
    # to be implemented here.


# dynamically add all metadata properties to the class
for k in VariableMeta.__dataclass_fields__:
    if hasattr(Variable, k):
        raise Exception(f'metadata field "{k}" would overwrite a Pandas built-in')

    setattr(Variable, k, metadata_property(k))


def _combine_variable_units_or_short_units(variables: List[Variable], operation, unit_or_short_unit) -> Optional[str]:
    # Gather units (or short units) of all variables.
    units_or_short_units = pd.unique([getattr(variable.metadata, unit_or_short_unit) for variable in variables])
    # Initialise the unit (or short unit) of the output variable.
    unit_or_short_unit_combined = None
    if operation in ["+", "-"]:
        # If units (or short units) do not coincide among all variables, raise a warning and assign None.
        if len(units_or_short_units) != 1:
            log.warning(f"Different values of '{unit_or_short_unit}' detected among variables: {units_or_short_units}")
            unit_or_short_unit_combined = None
        else:
            # Otherwise, assign the common unit.
            unit_or_short_unit_combined = units_or_short_units[0]

    return unit_or_short_unit_combined


def combine_variables_units(variables: List[Variable], operation: str) -> Optional[str]:
    return _combine_variable_units_or_short_units(variables=variables, operation=operation, unit_or_short_unit="unit")


def combine_variables_short_units(variables: List[Variable], operation) -> Optional[str]:
    return _combine_variable_units_or_short_units(
        variables=variables, operation=operation, unit_or_short_unit="short_unit"
    )


def _combine_variables_titles_and_descriptions(
    variables: List[Variable], operation: str, title_or_description: str
) -> Optional[str]:
    # Keep the title only if all variables have exactly the same title.
    # Otherwise we assume that the variable has a different meaning, and its title should be manually handled.
    title_or_description_combined = None
    if operation in ["+", "-", "fillna"]:
        titles_or_descriptions = pd.unique([getattr(variable.metadata, title_or_description) for variable in variables])
        if len(titles_or_descriptions) == 1:
            title_or_description_combined = titles_or_descriptions[0]

    return title_or_description_combined


def combine_variables_titles(variables: List[Variable], operation: str) -> Optional[str]:
    return _combine_variables_titles_and_descriptions(
        variables=variables, operation=operation, title_or_description="title"
    )


def combine_variables_descriptions(variables: List[Variable], operation: str) -> Optional[str]:
    return _combine_variables_titles_and_descriptions(
        variables=variables, operation=operation, title_or_description="description"
    )


def get_unique_sources_from_variables(variables: List[Variable]) -> List[Source]:
    # Make a list of all sources of all variables.
    sources = sum([variable.metadata.sources for variable in variables], [])

    # Get unique array of tuples of source fields (respecting the order).
    unique_sources_array = pd.unique([tuple(source.to_dict().items()) for source in sources])

    # Make a list of sources.
    unique_sources = [Source.from_dict(dict(source)) for source in unique_sources_array]  # type: ignore

    return unique_sources


def get_unique_licenses_from_variables(variables: List[Variable]) -> List[License]:
    # Make a list of all licenses of all variables.
    licenses = sum([variable.metadata.licenses for variable in variables], [])

    # Get unique array of tuples of license fields (respecting the order).
    unique_licenses_array = pd.unique([tuple(license.to_dict().items()) for license in licenses])

    # Make a list of licenses.
    unique_licenses = [License.from_dict(dict(license)) for license in unique_licenses_array]

    return unique_licenses


def combine_variables_processing_logs(variables: List[Variable]):
    # Make a list with all entries in the processing log of all variables.
    # TODO: Currently, the processing log is not catching the name of the new variable created, but the first variable
    #  involved in the operation.
    processing_log = sum(
        [
            variable.metadata.processing_log if variable.metadata.processing_log is not None else []
            for variable in variables
        ],
        [],
    )
    # TODO: For commutative operations ()

    return processing_log


def combine_variables_metadata(
    variables: List[Any], operation: str, name: Optional[str] = UNNAMED_VARIABLE
) -> VariableMeta:
    # Initialise an empty metadata.
    metadata = VariableMeta()

    # Skip other objects passed in variables that may not contain metadata (e.g. a scalar).
    variables_only = [variable for variable in variables if hasattr(variable, "metadata")]

    # Combine each metadata field using the logic of the specified operation.
    metadata.title = combine_variables_titles(variables=variables_only, operation=operation)
    metadata.description = combine_variables_descriptions(variables=variables_only, operation=operation)
    metadata.unit = combine_variables_units(variables=variables_only, operation=operation)
    metadata.short_unit = combine_variables_short_units(variables=variables_only, operation=operation)
    metadata.sources = get_unique_sources_from_variables(variables=variables_only)
    metadata.licenses = get_unique_licenses_from_variables(variables=variables_only)
    metadata.processing_log = combine_variables_processing_logs(variables=variables_only)

    # List names of variables and scalars (or other objects passed in variables).
    variables_and_scalars_names = [
        variable.name if hasattr(variable, "name") else str(variable) for variable in variables
    ]
    metadata.processing_log.extend([{"variable": name, "parents": variables_and_scalars_names, "operation": operation}])

    return metadata


def update_variable_name(variable, name):
    # Replace unnamed variable by the new variable name.
    # Say you have a table tb with variables tb["a"] and tb["b"]. If you create a new variable "c" as
    # variable_c = tb["a"] + tb["b"]
    # the new variable does not have a name ()
    # WARNING: This
    if hasattr(variable.metadata, "processing_log") and variable.metadata.processing_log is not None:
        variable.metadata.processing_log = json.loads(
            json.dumps(variable.metadata.processing_log).replace("**TEMPORARY UNNAMED VARIABLE**", name)
        )
    variable.name = name
