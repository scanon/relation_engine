import json
from typing import Union, Callable

from relation_engine_server.utils.json_validation import load_json_yaml
from relation_engine_server.utils import arango_client
from spec.validate import get_schema_type_paths


def get_local_coll_indexes():
    """
    Read all schemas for the collection schema type
    Return just collection name and indexes
    """
    coll_spec_paths = []
    coll_name_2_indexes = {}
    for coll_spec_path in get_schema_type_paths("collection"):
        coll = load_json_yaml(coll_spec_path)
        if "indexes" not in coll:
            continue
        coll_spec_paths.append(coll_spec_path)
        coll_name_2_indexes[coll["name"]] = coll["indexes"]
    return coll_spec_paths, coll_name_2_indexes


def ensure_indexes():
    """
    Returns tuple
    First item is list of borked index names, e.g.
    [
        "coll_name_3/fulltext/['scientific_name']",
        "coll_name_4/persistent/['id', 'key']",
    ]
    Second item is struct of failed indexes, e.g.,
    {
        coll_name_3: [
            {"type": "fulltext", "fields": ["scientific_name"] ...}
        ],
        coll_name_4: [
            {"type": "persistent", "fields": ["id", "key"] ...}
        ]
    }
    """
    coll_name_2_indexes_server = arango_client.get_all_indexes()
    coll_spec_paths, coll_name_2_indexes_local = get_local_coll_indexes()

    failed_specs = {}
    for coll_spec_path, (coll_name, indexes_local) in zip(
        coll_spec_paths, coll_name_2_indexes_local.items()
    ):
        print(f"Ensuring indexes for {coll_spec_path}")
        if coll_name not in coll_name_2_indexes_server:
            failed_specs[coll_name] = indexes_local
            continue
        else:
            failed_specs[coll_name] = []
        indexes_server = coll_name_2_indexes_server[coll_name]
        for index_local in indexes_local:
            match = False
            for index_server in indexes_server:
                if index_local.items() <= index_server.items():
                    match = True
                    break
            if match is False:
                failed_specs[coll_name] = index_local

    failed_specs = {
        k: v for k, v in failed_specs.items() if v
    }  # filter out 0-failure colls
    if failed_specs:
        print_failed_vs_server("indexes", failed_specs, coll_name_2_indexes_server)
    else:
        print("All index specs ensured")

    return get_names(failed_specs, "indexes"), failed_specs


def ensure_views():
    """
    Returns tuple
    First item is list of failed view names, e.g.,
    [
       "Compounds/arangosearch"
    ]
    Second item is list of failed specs, e.g.,
    [
        {"name": "Compounds", "type": "arangosearch", ...}
    ]
    """
    all_views_server = arango_client.get_all_views()
    mod_obj_literal(all_views_server, float, round_float)
    view_spec_paths = get_schema_type_paths("view")

    failed_specs = []
    for view_spec_path in view_spec_paths:
        print(f"Ensuring view {view_spec_path}")
        view_local = load_json_yaml(view_spec_path)
        match = False
        for view_server in all_views_server:
            if view_local.items() <= view_server.items():
                match = True
                break
        if match is False:
            failed_specs.append(view_local)

    if failed_specs:
        print_failed_vs_server("views", failed_specs, all_views_server)
    else:
        print("All view specs ensured")

    return get_names(failed_specs, "views"), failed_specs


def ensure_analyzers():
    """
    Returns tuple
    First item is list of failed view names, e.g.,
    [
       "icu_tokenize/text"
    ]
    Second item is list of failed specs, e.g.,
    [
        {"name": "icu_tokenize", "type": "text", ...}
    ]
    """
    all_analyzers_server = arango_client.get_all_analyzers()
    mod_obj_literal(all_analyzers_server, str, excise_namespace)
    analyzer_spec_paths = get_schema_type_paths("analyzer")

    failed_specs = []
    for analyzer_spec_path in analyzer_spec_paths:
        print(f"Ensuring analyzer {analyzer_spec_path}")
        analyzer_local = load_json_yaml(analyzer_spec_path)
        for analyzer_server in all_analyzers_server:
            match = False
            if analyzer_local.items() <= analyzer_server.items():
                match = True
                break
        if match is False:
            failed_specs.append(analyzer_local)

    if failed_specs:
        print_failed_vs_server("analyzers", failed_specs, all_analyzers_server)
    else:
        print("All analyzer specs ensured")

    return get_names(failed_specs, "analyzers"), failed_specs


def ensure_all():
    """
    Return names of failed specs if any, e.g.,
    {
        "indexes": [
        ],
        "views": [
            "Coumpounds/arangosearch",
            "Reactions/arangosearch",
        ],
        "analyzers": [
            "icu_tokenize/text",
        ],
    }
    """
    failed_indexes_names, _ = ensure_indexes()
    failed_views_names, _ = ensure_views()
    failed_analyzers_names, _ = ensure_analyzers()

    return {
        "indexes": failed_indexes_names,
        "views": failed_views_names,
        "analyzers": failed_analyzers_names,
    }


def get_names(specs, schema_type):
    """
    Given views/analyzers/collections, collate names using required properties
    """
    names = []
    if schema_type in ["views", "analyzers"]:
        for spec in specs:
            names.append(f"{spec['name']}/{spec['type']}")
    elif schema_type in ["indexes"]:
        for coll_name, indexes in specs.items():
            for index in indexes:
                names.append(f"{coll_name}/{index['type']}/{index['fields']}")
    else:
        raise RuntimeError(f'Unknown schema type "{schema_type}"')
    return names


def print_failed_vs_server(schema_type, failed_specs, server_specs):
    """
    Print message with names and contents of failed local specs and all server specs
    """
    dec = "*" * 80

    def format_json(jo):
        return json.dumps(jo, indent=4)

    fail_msg = (
        dec + "\n"
        f"----------> failed ({len(failed_specs)} {schema_type}) ---------->"
        "\n"
        f"----------> names: {get_names(failed_specs, schema_type)} ---------->"
        "\n" + format_json(failed_specs) + "\n"
        f"----------> server ({len(server_specs)} {schema_type}) ---------->"
        "\n"
        f"----------> names: {get_names(server_specs, schema_type)} ---------->"
        "\n" + format_json(server_specs) + "\n" + dec
    )

    print(fail_msg)


def round_float(num: float) -> float:
    """
    For round-off error in floats
    Arbitrarily chose 7 places
    """
    return round(num, 7)


def excise_namespace(analyzer_name: str) -> str:
    """
    Remove namespace prefix, e.g.,
    namespace::thing -> thing
    """
    return analyzer_name.split("::")[-1]


def mod_obj_literal(
    spec_unit: Union[list, dict],
    literal_type: type,
    func: Callable[[Union[float, str]], Union[float, str]],
) -> None:
    """
    Modify dict in-place recursively
    Some specs won't match because of
    * round-off error in floats
    * namespacing in analyzers, e.g., "_system::icu_tokenize"

    Parameters
    ----------
    spec_unit -     recursively accessed data structure unit of JSON obj
    literal_type -  str or float
    func -          function called to modify that str or float in-place
    """
    if isinstance(spec_unit, dict):
        for k, v in spec_unit.items():
            if isinstance(v, dict) or isinstance(v, list):
                mod_obj_literal(v, literal_type, func)
            elif isinstance(v, literal_type):
                spec_unit[k] = func(v)  # type: ignore
    elif isinstance(spec_unit, list):
        for i, v in enumerate(spec_unit):
            if isinstance(v, dict) or isinstance(v, list):
                mod_obj_literal(v, literal_type, func)
            elif isinstance(v, literal_type):
                spec_unit[i] = func(v)  # type: ignore
