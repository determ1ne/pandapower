# -*- coding: utf-8 -*-

# Copyright (c) 2016-2023 by University of Kassel and Fraunhofer Institute for Energy Economics
# and Energy System Technology (IEE), Kassel. All rights reserved.


import copy

import numpy as np
import pandas as pd
from pandas._testing import assert_series_equal
import pytest

import pandapower as pp
import pandapower.networks as nw
import pandapower.toolbox as tb
import pandapower.control
from pandapower.test.toolbox import assert_net_equal


def test_element_bus_tuples():
    ebts = pp.element_bus_tuples()
    assert isinstance(ebts, set)
    assert len(ebts) >= 20
    item = next(iter(ebts))
    assert isinstance(item, tuple)
    assert len(item) == 2
    assert len({"line", "gen"} & {elm for (elm, bus) in ebts}) == 2
    assert {bus for (elm, bus) in ebts} == {"bus", "to_bus", "from_bus", 'hv_bus', 'mv_bus',
                                            'lv_bus'}
    assert len(pp.element_bus_tuples(bus_elements=False, res_elements=True)) > \
           1.5 * len(pp.element_bus_tuples(bus_elements=False, res_elements=False)) > 0


def test_pp_elements():
    elms = pp.pp_elements()
    assert isinstance(elms, set)
    assert "bus" in elms
    assert "measurement" in elms
    assert "sgen" in elms
    assert len(pp.pp_elements(bus=False, other_elements=False, bus_elements=True,
                              branch_elements=False)) == \
           len(pp.element_bus_tuples(bus_elements=True, branch_elements=False))


def test_branch_element_bus_dict():
    bebd = pp.branch_element_bus_dict()
    assert isinstance(bebd, dict)
    assert len(bebd) >= 5
    assert set(bebd["trafo"]) == {"hv_bus", "lv_bus"}
    bebd = pp.branch_element_bus_dict(include_switch=True)
    assert "bus" in bebd["switch"]


def test_signing_system_value():
    assert pp.signing_system_value("sgen") == -1
    assert pp.signing_system_value("load") == 1
    for bus_elm in pp.pp_elements(bus=False, branch_elements=False, other_elements=False):
        assert pp.signing_system_value(bus_elm) in [1, -1]
    try:
        pp.signing_system_value("sdfjio")
        assert False
    except ValueError:
        pass


def test_opf_task():
    net = pp.create_empty_network()
    pp.create_buses(net, 6, [10, 10, 10, 0.4, 7, 7],
                    min_vm_pu=[0.9, 0.9, 0.88, 0.9, np.nan, np.nan])
    idx_ext_grid = 1
    pp.create_ext_grid(net, 0, max_q_mvar=80, min_p_mw=0, index=idx_ext_grid)
    pp.create_gen(net, 1, 10, min_q_mvar=-50, max_q_mvar=-10, min_p_mw=0, max_p_mw=60)
    pp.create_gen(net, 2, 8)
    pp.create_gen(net, 3, 5)
    pp.create_load(net, 3, 120, max_p_mw=8)
    pp.create_sgen(net, 1, 8, min_q_mvar=-50, max_q_mvar=-10, controllable=False)
    pp.create_sgen(net, 2, 8)
    pp.create_storage(net, 3, 2, 100, min_q_mvar=-10, max_q_mvar=-50, min_p_mw=0, max_p_mw=60,
                      controllable=True)
    pp.create_dcline(net, 4, 5, 0.3, 1e-4, 1e-2, 1.01, 1.02, min_q_from_mvar=-10,
                     min_q_to_mvar=-10)
    pp.create_line(net, 3, 4, 5, "122-AL1/20-ST1A 10.0", max_loading_percent=50)
    pp.create_transformer(net, 2, 3, "0.25 MVA 10/0.4 kV")

    # --- run and check opf_task()
    out1 = pp.opf_task(net, keep=True)
    assert out1["flexibilities_without_costs"] == "all"
    assert sorted(out1["flexibilities"].keys()) == [i1 + i2 for i1 in ["P", "Q"] for i2 in [
        "dcline", "ext_grid", "gen", "storage"]]
    for key, df in out1["flexibilities"].items():
        assert df.shape[0]
        if "gen" in key:
            assert df.shape[0] > 1
    assert out1["flexibilities"]["Pext_grid"].loc[0, "index"] == [1]
    assert np.isnan(out1["flexibilities"]["Pext_grid"].loc[0, "max"])
    assert out1["flexibilities"]["Pext_grid"].loc[0, "min"] == 0
    assert np.isnan(out1["flexibilities"]["Qext_grid"].loc[0, "min"])
    assert out1["flexibilities"]["Qext_grid"].loc[0, "max"] == 80
    assert sorted(out1["network_constraints"].keys()) == ["LOADINGline", "VMbus"]
    assert out1["network_constraints"]["VMbus"].shape[0] == 3

    # check delta_pq
    net.gen.loc[0, "min_p_mw"] = net.gen.loc[0, "max_p_mw"] - 1e-5
    out2 = pp.opf_task(net, delta_pq=1e-3, keep=True)
    assert out2["flexibilities"]["Pgen"].shape[0] == 1

    net.gen.loc[0, "min_p_mw"] = net.gen.loc[0, "max_p_mw"] - 1e-1
    out1["flexibilities"]["Pgen"].loc[0, "min"] = out1["flexibilities"]["Pgen"].loc[
                                                      0, "max"] - 1e-1
    out3 = pp.opf_task(net, delta_pq=1e-3, keep=True)
    for key in out3["flexibilities"]:
        assert pp.dataframes_equal(out3["flexibilities"][key], out1["flexibilities"][key])

    # check costs
    pp.create_poly_cost(net, idx_ext_grid, "ext_grid", 2)
    pp.create_poly_cost(net, 1, "gen", 1.7)
    pp.create_poly_cost(net, 0, "dcline", 2)
    pp.create_pwl_cost(net, 2, "gen", [[-1e9, 1, 3.1], [1, 1e9, 0.5]], power_type="q")
    out4 = pp.opf_task(net)
    for dict_key in ["flexibilities", "network_constraints"]:
        for key in out4[dict_key]:
            assert pp.dataframes_equal(out4[dict_key][key], out1[dict_key][key])
    assert isinstance(out4["flexibilities_without_costs"], dict)
    expected_elm_without_cost = ["gen", "storage"]
    assert sorted(out4["flexibilities_without_costs"].keys()) == expected_elm_without_cost
    for elm in expected_elm_without_cost:
        assert len(out4["flexibilities_without_costs"][elm]) == 1


def test_nets_equal():
    tb.logger.setLevel(40)
    original = nw.create_cigre_network_lv()
    net = copy.deepcopy(original)

    # should be equal
    assert tb.nets_equal(original, net)
    assert tb.nets_equal(net, original)

    # detecting additional element
    pp.create_bus(net, vn_kv=.4)
    assert not tb.nets_equal(original, net)
    assert not tb.nets_equal(net, original)
    net = copy.deepcopy(original)

    # detecting removed element
    net["bus"].drop(net.bus.index[0], inplace=True)
    assert not tb.nets_equal(original, net)
    assert not tb.nets_equal(net, original)
    net = copy.deepcopy(original)

    # detecting alternated value
    net["load"]["p_mw"][net["load"].index[0]] += 0.1
    assert not tb.nets_equal(original, net)
    assert not tb.nets_equal(net, original)
    net = copy.deepcopy(original)

    # detecting added column
    net["load"]["new_col"] = 0.1
    assert not tb.nets_equal(original, net)
    assert not tb.nets_equal(net, original)
    net = copy.deepcopy(original)

    # not detecting alternated value if difference is beyond tolerance
    net["load"]["p_mw"][net["load"].index[0]] += 0.0001
    assert tb.nets_equal(original, net, atol=0.1)
    assert tb.nets_equal(net, original, atol=0.1)

    # check controllers
    original.trafo.tap_side.fillna("hv", inplace=True)
    net1 = original.deepcopy()
    net2 = original.deepcopy()
    pp.control.ContinuousTapControl(net1, 0, 1.0)
    pp.control.ContinuousTapControl(net2, 0, 1.0)
    c1 = net1.controller.at[0, "object"]
    c2 = net2.controller.at[0, "object"]
    assert c1 == c2
    assert c1 is not c2
    assert tb.nets_equal(net1, net2)
    c1.vm_set_pu = 1.01
    assert c1 != c2
    assert tb.nets_equal(net1, net2, exclude_elms=["controller"])
    assert not tb.nets_equal(net1, net2)


def test_clear_result_tables():
    net = nw.case9()
    pp.runpp(net)
    elms_to_check = ["bus", "line", "load"]
    for elm in elms_to_check:
        assert net["res_%s" % elm].shape[0]
    pp.clear_result_tables(net)
    for elm in elms_to_check:
        assert not net["res_%s" % elm].shape[0]


def test_add_column_from_node_to_elements():
    net = nw.create_cigre_network_mv("pv_wind")
    net.bus["subnet"] = ["subnet_%i" % i for i in range(net.bus.shape[0])]
    net.sgen["subnet"] = "already_given"
    net.switch["subnet"] = None
    net_orig = copy.deepcopy(net)

    branch_bus = ["from_bus", "lv_bus"]
    pp.add_column_from_node_to_elements(net, "subnet", False, branch_bus=branch_bus)

    def check_subnet_correctness(ntw, elements, branch_bus_el):
        for elm in elements:
            if "bus" in ntw[elm].columns:
                assert all(pp.compare_arrays(ntw[elm]["subnet"].values,
                                             np.array(["subnet_%i" % bus for bus in ntw[elm].bus])))
            elif branch_bus_el[0] in ntw[elm].columns:
                assert all(pp.compare_arrays(ntw[elm]["subnet"].values, np.array([
                    "subnet_%i" % bus for bus in ntw[elm][branch_bus_el[0]]])))
            elif branch_bus_el[1] in ntw[elm].columns:
                assert all(pp.compare_arrays(ntw[elm]["subnet"].values, np.array([
                    "subnet_%i" % bus for bus in ntw[elm][branch_bus_el[1]]])))

    check_subnet_correctness(net, pp.pp_elements(bus=False) - {"sgen"}, branch_bus)

    pp.add_column_from_node_to_elements(net_orig, "subnet", True, branch_bus=branch_bus)
    check_subnet_correctness(net_orig, pp.pp_elements(bus=False), branch_bus)


def test_add_column_from_element_to_elements():
    net = nw.create_cigre_network_mv()
    pp.create_measurement(net, "i", "trafo", 5, 3, 0, side="hv")
    pp.create_measurement(net, "i", "line", 5, 3, 0, side="to")
    pp.create_measurement(net, "p", "bus", 5, 3, 2)
    assert net.measurement.name.isnull().all()
    assert ~net.switch.name.isnull().all()
    orig_switch_names = copy.deepcopy(net.switch.name.values)
    expected_measurement_names = np.array([
        net.trafo.name.loc[0], net.line.name.loc[0], net.bus.name.loc[2]])
    expected_switch_names = np.append(
        net.line.name.loc[net.switch.element.loc[net.switch.et == "l"]].values,
        net.trafo.name.loc[net.switch.element.loc[net.switch.et == "t"]].values)

    pp.add_column_from_element_to_elements(net, "name", False)
    assert all(pp.compare_arrays(net.measurement.name.values, expected_measurement_names))
    assert all(pp.compare_arrays(net.switch.name.values, orig_switch_names))

    del net.measurement["name"]
    pp.add_column_from_element_to_elements(net, "name", True)
    assert all(pp.compare_arrays(net.measurement.name.values, expected_measurement_names))
    assert all(pp.compare_arrays(net.switch.name.values, expected_switch_names))


def test_reindex_buses():
    net_orig = nw.example_simple()
    net = nw.example_simple()

    to_add = 5
    new_bus_idxs = np.array(list(net.bus.index)) + to_add
    bus_lookup = dict(zip(net["bus"].index.values, new_bus_idxs))
    # a more complexe bus_lookup of course should also work, but this one is easy to check
    pp.reindex_buses(net, bus_lookup)

    for elm in net.keys():
        if isinstance(net[elm], pd.DataFrame) and net[elm].shape[0]:
            cols = pd.Series(net[elm].columns)
            bus_cols = cols.loc[cols.str.contains("bus")]
            if len(bus_cols):
                for bus_col in bus_cols:
                    assert all(net[elm][bus_col] == net_orig[elm][bus_col] + to_add)
            if elm == "bus":
                assert all(np.array(list(net[elm].index)) == np.array(list(
                    net_orig[elm].index)) + to_add)


def test_continuos_bus_numbering():
    net = pp.create_empty_network()

    bus0 = pp.create_bus(net, 0.4, index=12)
    pp.create_load(net, bus0, p_mw=0.)
    pp.create_load(net, bus0, p_mw=0.)
    pp.create_load(net, bus0, p_mw=0.)
    pp.create_load(net, bus0, p_mw=0.)

    bus0 = pp.create_bus(net, 0.4, index=42)
    pp.create_sgen(net, bus0, p_mw=0.)
    pp.create_sgen(net, bus0, p_mw=0.)
    pp.create_sgen(net, bus0, p_mw=0.)

    bus0 = pp.create_bus(net, 0.4, index=543)
    pp.create_shunt(net, bus0, 2, 1)
    pp.create_shunt(net, bus0, 2, 1)
    pp.create_shunt(net, bus0, 2, 1)

    bus0 = pp.create_bus(net, 0.4, index=5675)
    pp.create_ward(net, bus0, 2, 1, 1, 2)
    pp.create_ward(net, bus0, 2, 1, 1, 2)
    pp.create_ward(net, bus0, 2, 1, 1, 2)

    tb.create_continuous_bus_index(net)

    buses = net.bus.index
    assert all(buses[i] <= buses[i + 1] for i in range(len(buses) - 1))  # is ordered
    assert all(buses[i] + 1 == buses[i + 1] for i in range(len(buses) - 1))  # is consecutive
    assert buses[0] == 0  # starts at zero

    used_buses = []
    for element in net.keys():
        try:
            used_buses.extend(net[element].bus.values)
        except AttributeError:
            try:
                used_buses.extend(net[element].from_bus.values)
                used_buses.extend(net[element].to_bus.values)
            except AttributeError:
                try:
                    used_buses.extend(net[element].hv_bus.values)
                    used_buses.extend(net[element].lv_bus.values)
                except AttributeError:
                    continue

    # assert that no buses were used except the ones in net.bus
    assert set(list(used_buses)) - set(list(net.bus.index.values)) == set()


def test_reindex_elements():
    net = nw.example_simple()

    new_sw_idx = [569, 763, 502, 258, 169, 259, 348, 522]
    pp.reindex_elements(net, "switch", new_sw_idx)
    assert np.allclose(net.switch.index.values, new_sw_idx)

    net2 = copy.deepcopy(net)

    previous_idx = new_sw_idx[:3]
    new_sw_idx = [2, 3, 4]
    pp.reindex_elements(net, "switch", new_sw_idx, previous_idx)
    assert np.allclose(net.switch.index.values[:3], new_sw_idx)

    # using lookup
    pp.reindex_elements(net2, "switch", lookup=dict(zip(previous_idx, new_sw_idx)))
    assert_net_equal(net, net2)

    pp.reindex_elements(net, "line", [77, 22], [2, 0])
    assert np.allclose(net.line.index.values, [22, 1, 77, 3])
    assert np.allclose(net.switch.element.iloc[[4, 5]], [77, 77])

    old_idx = copy.deepcopy(net.bus.index.values)
    pp.reindex_elements(net, "bus", old_idx + 2)
    assert np.allclose(net.bus.index.values, old_idx + 2)

    pp.reindex_elements(net, "bus", [400, 600], [4, 6])
    assert 400 in net.bus.index
    assert 600 in net.bus.index


def test_continuous_element_numbering():
    from pandapower.estimation.util import add_virtual_meas_from_loadflow
    net = nw.example_multivoltage()

    # Add noises to index with some large number
    net.line.rename(index={4: 280}, inplace=True)
    net.trafo.rename(index={0: 300}, inplace=True)
    net.trafo.rename(index={1: 400}, inplace=True)
    net.trafo3w.rename(index={0: 540}, inplace=True)

    net.switch.loc[(net.switch.et == "l") & (net.switch.element == 4), "element"] = 280
    net.switch.loc[(net.switch.et == "t") & (net.switch.element == 0), "element"] = 300
    net.switch.loc[(net.switch.et == "t") & (net.switch.element == 1), "element"] = 400
    pp.runpp(net)
    add_virtual_meas_from_loadflow(net)
    assert net.measurement["element"].max() == 540

    tb.create_continuous_elements_index(net)
    assert net.line.index.max() == net.line.shape[0] - 1
    assert net.trafo.index.max() == net.trafo.shape[0] - 1
    assert net.trafo3w.index.max() == net.trafo3w.shape[0] - 1
    assert net.measurement["element"].max() == net.bus.shape[0] - 1


def test_scaling_by_type():
    net = pp.create_empty_network()

    bus0 = pp.create_bus(net, 0.4)
    pp.create_load(net, bus0, p_mw=0., type="Household")
    pp.create_sgen(net, bus0, p_mw=0., type="PV")

    tb.set_scaling_by_type(net, {"Household": 42., "PV": 12})

    assert net.load.at[0, "scaling"] == 42
    assert net.sgen.at[0, "scaling"] == 12

    tb.set_scaling_by_type(net, {"Household": 0, "PV": 0})

    assert net.load.at[0, "scaling"] == 0
    assert net.sgen.at[0, "scaling"] == 0


def test_drop_inactive_elements():
    for service in (False, True):
        net = pp.create_empty_network()
        bus_sl = pp.create_bus(net, vn_kv=.4, in_service=service)
        pp.create_ext_grid(net, bus_sl, in_service=service)
        bus0 = pp.create_bus(net, vn_kv=.4, in_service=service)
        pp.create_switch(net, bus_sl, bus0, 'b', not service)
        bus1 = pp.create_bus(net, vn_kv=.4, in_service=service)
        pp.create_transformer(net, bus0, bus1, in_service=service,
                              std_type='63 MVA 110/20 kV')
        bus2 = pp.create_bus(net, vn_kv=.4, in_service=service)
        pp.create_line(net, bus1, bus2, length_km=1, in_service=service,
                       std_type='149-AL1/24-ST1A 10.0')
        pp.create_load(net, bus2, p_mw=0., in_service=service)
        pp.create_sgen(net, bus2, p_mw=0., in_service=service)
        bus3 = pp.create_bus(net, vn_kv=.4, in_service=service)
        bus4 = pp.create_bus(net, vn_kv=.4, in_service=service)
        pp.create_transformer3w_from_parameters(net, bus2, bus3, bus4, 0.4, 0.4, 0.4, 100, 50, 50,
                                                3, 3, 3, 1, 1, 1, 5, 1)
        # drop them
        tb.drop_inactive_elements(net)

        sum_of_elements = 0
        for element, table in net.items():
            # skip this one since we expect items here
            if element.startswith("_") or not isinstance(table, pd.DataFrame):
                continue
            try:
                if service and (element == 'ext_grid' or (element == 'bus' and len(net.bus) == 1)):
                    # if service==True, the 1 ext_grid and its bus are not dropped
                    continue
                if len(table) > 0:
                    sum_of_elements += len(table)
                    # print(element)
            except TypeError:
                # _ppc is initialized with None and clashes when checking
                continue

        assert sum_of_elements == 0
        if service:
            assert len(net.ext_grid) == 1
            assert len(net.bus) == 1
            assert bus_sl in net.bus.index.values

    net = pp.create_empty_network()

    bus0 = pp.create_bus(net, vn_kv=.4, in_service=True)
    pp.create_ext_grid(net, bus0, in_service=True)
    bus1 = pp.create_bus(net, vn_kv=.4, in_service=False)
    pp.create_line(net, bus0, bus1, length_km=1, in_service=False,
                   std_type='149-AL1/24-ST1A 10.0')
    gen0 = pp.create_gen(net, bus=bus1, p_mw=0.001)

    tb.drop_inactive_elements(net)

    assert gen0 not in net.gen.index


def test_get_connected_lines_at_bus():
    net = pp.create_empty_network()

    bus0 = pp.create_bus(net, 0.4)
    bus1 = pp.create_bus(net, 0.4)

    line0 = pp.create_line(net, bus0, bus1, length_km=1., std_type="NAYY 4x50 SE")
    line1 = pp.create_line(net, bus0, bus1, length_km=1., std_type="NAYY 4x50 SE")
    line2 = pp.create_line(net, bus0, bus1, in_service=False, length_km=1., std_type="NAYY 4x50 SE")
    line3 = pp.create_line(net, bus0, bus1, length_km=1., std_type="NAYY 4x50 SE")

    pp.create_switch(net, bus0, line0, "l")
    pp.create_switch(net, bus0, line1, "l", closed=False)
    pp.create_switch(net, bus0, line2, "l")

    lines = tb.get_connected_elements(net, "line", bus0, respect_switches=False,
                                      respect_in_service=False)

    assert set(lines) == {line0, line1, line2, line3}

    lines = tb.get_connected_elements(net, "line", bus0, respect_switches=True,
                                      respect_in_service=False)
    assert set(lines) == {line0, line2, line3}

    lines = tb.get_connected_elements(net, "line", bus0, respect_switches=True,
                                      respect_in_service=True)
    assert set(lines) == {line0, line3}

    lines = tb.get_connected_elements(net, "line", bus0, respect_switches=False,
                                      respect_in_service=True)
    assert set(lines) == {line0, line1, line3}


def test_merge_indices():
    net1 = nw.create_cigre_network_mv()
    pp.create_pwl_cost(net1, 0, "load", [[0, 20, 1], [20, 30, 2]])
    pp.create_pwl_cost(net1, 2, "load", [[0, 20, 0.5], [20, 30, 2]])
    pp.reindex_buses(net1, {3: 29})
    assert 29 in net1.bus.index.values
    assert 29 in net1.load.bus.values

    net2 = nw.create_cigre_network_mv(with_der="pv_wind")
    pp.create_pwl_cost(net2, 1, "load", [[0, 20, 1], [20, 30, 2]], index=5)
    pp.create_pwl_cost(net2, 2, "sgen", [[0, 20, 0.5], [20, 30, 2]])

    net = pp.merge_nets(net1, net2, net2_reindex_log_level="debug")

    # check
    for et in pp.pp_elements(cost_tables=True):
        assert net[et].shape[0] == net1[et].shape[0] + net2[et].shape[0]
    assert net.bus.index.tolist() == net1.bus.index.tolist() + [
        i+29+1 if i < 3 else i+29 if i > 3 else 3 for i in net2.bus.index]
    assert net.load.index.tolist() == list(range(net.load.shape[0]))
    assert net.load.bus.tolist() == net1.load.bus.tolist() + [
        i+29+1 if i < 3 else i+29 if i > 3 else 3 for i in net2.load.bus]
    assert net.pwl_cost.index.tolist() == [0, 1, 5, 6]
    assert net.pwl_cost.element.tolist() == [0, 2, 19, 2]
    assert net.pwl_cost.et.tolist() == ["load"]*3 + ["sgen"]


def test_merge_and_split_nets():
    net1 = nw.mv_oberrhein()
    pp.create_poly_cost(net1, 2, "sgen", 8)
    pp.create_poly_cost(net1, 0, "sgen", 9)
    # TODO there are some geodata values in oberrhein without corresponding lines
    net1.line_geodata.drop(set(net1.line_geodata.index) - set(net1.line.index), inplace=True)
    n1 = len(net1.bus)
    pp.runpp(net1)
    net2 = nw.create_cigre_network_mv(with_der="pv_wind")
    pp.create_poly_cost(net2, 3, "sgen", 10)
    pp.create_poly_cost(net2, 0, "sgen", 11)
    pp.runpp(net2)

    net1_before = copy.deepcopy(net1)
    net2_before = copy.deepcopy(net2)
    net = pp.merge_nets(net1, net2, net2_reindex_log_level="debug")
    pp.runpp(net)

    # check that merge_nets() doesn't change inputs (but result tables)
    assert_net_equal(net1, net1_before, check_without_results=True)
    assert_net_equal(net2, net2_before, check_without_results=True)

    # check that results of merge_nets() fit
    assert np.allclose(net.res_bus.vm_pu.iloc[:n1].values, net1.res_bus.vm_pu.values)
    assert np.allclose(net.res_bus.vm_pu.iloc[n1:].values, net2.res_bus.vm_pu.values)

    # check content of merge_nets() output
    assert np.array_equal(
        pd.concat([net1.sgen.name.loc[net1.poly_cost.element],
                   net2.sgen.name.loc[net2.poly_cost.element]]).values,
        net.sgen.name.loc[net.poly_cost.element].values)

    # check that results stay the same after net split
    net3 = pp.select_subnet(net, net.bus.index[:n1], include_results=True)
    assert pp.dataframes_equal(net3.res_bus[["vm_pu"]], net1.res_bus[["vm_pu"]])

    net4 = pp.select_subnet(net, net.bus.index[n1:], include_results=True)
    assert np.allclose(net4.res_bus.vm_pu.values, net2.res_bus.vm_pu.values)


def test_merge_asymmetric():
    """Test that merging nets properly handles bus IDs for asymmetric elements
    """
    net1 = nw.ieee_european_lv_asymmetric()
    net2 = nw.ieee_european_lv_asymmetric()
    n_load_busses = len(net1.asymmetric_load.bus.unique())
    n_sgen_busses = len(net1.asymmetric_sgen.bus.unique())

    net1_before = copy.deepcopy(net1)
    net2_before = copy.deepcopy(net2)
    net = pp.merge_nets(net1, net2, net2_reindex_log_level="debug")

    assert_net_equal(net1, net1_before, check_without_results=True)
    assert_net_equal(net2, net2_before, check_without_results=True)
    assert len(net.asymmetric_load.bus.unique()) == 2 * n_load_busses
    assert len(net.asymmetric_sgen.bus.unique()) == 2 * n_sgen_busses


def test_merge_with_groups():
    """Test that group data are correctly considered by merge_nets()
    """
    net1 = nw.create_cigre_network_mv()
    net2 = nw.create_cigre_network_mv()
    for elm in ["bus", "load", "line"]:
        net2[elm].name = "new " + net2[elm].name
    pp.create_group(net1, "bus", [[0, 2]], name="group of net1")
    pp.create_group(net2, ["bus", "load"], [[1], [0, 3]], name="group1 of net2")
    pp.create_group(net2, ["line"], [[1, 3]], name="group2 of net2", index=4)

    net = pp.merge_nets(net1, net2, net2_reindex_log_level="debug")

    # check that all group lines are available
    assert net.group.shape[0] == net1.group.shape[0] + net2.group.shape[0]

    # check (adapted) index
    assert set(net.group.index) == {0, 5, 4}

    # check that net2 groups link to the same elements as later in net.group (checking by element names)
    assert net2.bus.name.loc[pp.group_element_index(net2, 0, "bus")].tolist() == \
        net.bus.name.loc[pp.group_element_index(net, 5, "bus")].tolist()
    assert net2.load.name.loc[pp.group_element_index(net2, 0, "load")].tolist() == \
        net.load.name.loc[pp.group_element_index(net, 5, "load")].tolist()
    assert net2.trafo.name.loc[pp.group_element_index(net2, 4, "trafo")].tolist() == \
        net.trafo.name.loc[pp.group_element_index(net, 4, "trafo")].tolist()

    # check that net2 groups link to the same elements as later in net.group (checking by element index)
    assert list(pp.group_element_index(net, 0, "bus")) == [0, 2]
    assert list(pp.group_element_index(net, 5, "bus")) == [net1.bus.shape[0]+1]
    assert list(pp.group_element_index(net, 5, "load")) == list(np.array([0, 3], dtype=int) + \
        net1.load.shape[0])
    assert list(pp.group_element_index(net, 4, "line")) == list(np.array([1, 3], dtype=int) + \
        net1.line.shape[0])


def test_select_subnet():
    # This network has switches of type 'l' and 't'
    net = nw.create_cigre_network_mv()

    # Do nothing
    same_net = pp.select_subnet(net, net.bus.index)
    assert pp.dataframes_equal(net.bus, same_net.bus)
    assert pp.dataframes_equal(net.switch, same_net.switch)
    assert pp.dataframes_equal(net.trafo, same_net.trafo)
    assert pp.dataframes_equal(net.line, same_net.line)
    assert pp.dataframes_equal(net.load, same_net.load)
    assert pp.dataframes_equal(net.ext_grid, same_net.ext_grid)
    same_net2 = pp.select_subnet(net, net.bus.index, include_results=True,
                                 keep_everything_else=True)
    assert pp.nets_equal(net, same_net2)

    # Remove everything
    empty = pp.select_subnet(net, set())
    assert len(empty.bus) == 0
    assert len(empty.line) == 0
    assert len(empty.load) == 0
    assert len(empty.trafo) == 0
    assert len(empty.switch) == 0
    assert len(empty.ext_grid) == 0

    # Should keep all trafo ('t') switches when buses are included
    hv_buses = set(net.trafo.hv_bus)
    lv_buses = set(net.trafo.lv_bus)
    trafo_switch_buses = set(net.switch[net.switch.et == 't'].bus)
    subnet = pp.select_subnet(net, hv_buses | lv_buses | trafo_switch_buses)
    assert net.switch[net.switch.et == 't'].index.isin(subnet.switch.index).all()

    # Should keep all line ('l') switches when buses are included
    from_bus = set(net.line.from_bus)
    to_bus = set(net.line.to_bus)
    line_switch_buses = set(net.switch[net.switch.et == 'l'].bus)
    subnet = pp.select_subnet(net, from_bus | to_bus | line_switch_buses)
    assert net.switch[net.switch.et == 'l'].index.isin(subnet.switch.index).all()
    ls = net.switch.loc[net.switch.et == "l"]
    subnet = pp.select_subnet(net, list(ls.bus.values)[::2], include_switch_buses=True)
    assert net.switch[net.switch.et == 'l'].index.isin(subnet.switch.index).all()
    assert net.switch[net.switch.et == 'l'].bus.isin(subnet.bus.index).all()

    # This network has switches of type 'b'
    net2 = nw.create_cigre_network_lv()

    # Should keep all bus-to-bus ('b') switches when buses are included
    buses = set(net2.switch[net2.switch.et == 'b'].bus)
    elements = set(net2.switch[net2.switch.et == 'b'].element)
    subnet = pp.select_subnet(net2, buses | elements)
    assert net2.switch[net2.switch.et == 'b'].index.isin(subnet.switch.index).all()


def test_overloaded_lines():
    net = pp.create_empty_network()

    bus0 = pp.create_bus(net, vn_kv=.4)
    bus1 = pp.create_bus(net, vn_kv=.4)

    pp.create_ext_grid(net, bus0)

    line0 = pp.create_line(net, bus0, bus1, length_km=1, std_type="NAYY 4x50 SE")
    line1 = pp.create_line(net, bus0, bus1, length_km=1, std_type="NA2XS2Y 1x95 RM/25 12/20 kV")
    line2 = pp.create_line(net, bus0, bus1, length_km=1, std_type="15-AL1/3-ST1A 0.4")
    pp.create_line(net, bus0, bus1, length_km=10, std_type="149-AL1/24-ST1A 10.0")

    pp.create_load(net, bus1, p_mw=0.2, q_mvar=0.05)

    pp.runpp(net)
    # test the overloaded lines by default value of max_load=100
    overloaded_lines = tb.overloaded_lines(net, max_load=100)

    assert set(overloaded_lines) == {line0, line1}

    # test the overloaded lines by a self defined value of max_load=50
    overloaded_lines = tb.overloaded_lines(net, max_load=50)

    assert set(overloaded_lines) == {line0, line1, line2}


def test_violated_buses():
    net = nw.create_cigre_network_lv()

    pp.runpp(net)

    # set the range of vm.pu
    min_vm_pu = 0.92
    max_vm_pu = 1.1

    # print out the list of violated_bus's index
    violated_bus = tb.violated_buses(net, min_vm_pu, max_vm_pu)

    assert set(violated_bus) == set(net["bus"].index[[16, 35, 36, 40]])


def test_add_zones_to_elements():
    net = nw.create_cigre_network_mv()

    # add zones to lines and switchs
    tb.add_zones_to_elements(net, elements=["line", "switch"])

    # create 2 arrays which include "zone" in lines and switches
    zone_line = net["line"]["zone"].values
    zone_switch = net["switch"]["zone"].values

    assert "CIGRE_MV" in zone_line
    assert "CIGRE_MV" in zone_switch


def test_drop_inner_branches():
    def check_elm_number(net1, net2, excerpt_elms=None):
        excerpt_elms = set() if excerpt_elms is None else set(excerpt_elms)
        for elm in set(pp.pp_elements()) - excerpt_elms:
            assert net1[elm].shape[0] == net2[elm].shape[0]

    net = nw.example_simple()
    new_bus = pp.create_bus(net, 10)
    pp.create_transformer3w(net, 2, 3, new_bus, "63/25/38 MVA 110/20/10 kV")

    net1 = copy.deepcopy(net)
    tb.drop_inner_branches(net1, [2, 3], branch_elements=["line"])
    check_elm_number(net1, net)
    tb.drop_inner_branches(net1, [0, 1], branch_elements=["line"])
    check_elm_number(net1, net, ["line"])
    assert all(net.line.index.difference({0}) == net1.line.index)

    net2 = copy.deepcopy(net)
    tb.drop_inner_branches(net2, [2, 3, 4, 5])
    assert all(net.line.index.difference({1}) == net2.line.index)
    assert all(net.trafo.index.difference({0}) == net2.trafo.index)
    assert all(net.switch.index.difference({1, 2, 3}) == net2.switch.index)
    check_elm_number(net2, net, ["line", "switch", "trafo"])


def test_fuse_buses():
    net = pp.create_empty_network()
    b1 = pp.create_bus(net, vn_kv=1, name="b1")
    b2 = pp.create_bus(net, vn_kv=1.5, name="b2")
    b3 = pp.create_bus(net, vn_kv=2, name="b2")

    line1 = pp.create_line(net, b2, b1, length_km=1, std_type="NAYY 4x50 SE")
    line2 = pp.create_line(net, b2, b3, length_km=1, std_type="NAYY 4x50 SE")

    sw1 = pp.create_switch(net, b2, line1, et="l")
    sw2 = pp.create_switch(net, b1, b2, et="b")

    pp.create_load(net, b1, p_mw=0.006)
    pp.create_load(net, b2, p_mw=0.005)
    pp.create_load(net, b3, p_mw=0.005)

    pp.create_measurement(net, "v", "bus", 1.2, 0.03, b2)

    # --- drop = True
    net1 = copy.deepcopy(net)
    tb.fuse_buses(net1, b1, b2, drop=True)

    # assertion: elements connected to b2 are given to b1 instead
    assert line1 not in net1.line.index
    assert line2 in net1.line.index
    assert sw1 not in net1.switch.index
    assert sw2 not in net1.switch.index
    assert list(net1["load"]["bus"].values) == [b1, b1, b3]
    assert net1["measurement"]["element"].at[0] == b1
    # assertion: b2 not in net.bus table if drop=True
    assert b2 not in net1.bus.index
    assert b3 in net1.bus.index

    # --- drop = False
    net2 = copy.deepcopy(net)
    tb.fuse_buses(net2, b1, b2, drop=False)

    # assertion: elements connected to b2 are given to b1 instead
    assert net2["line"]["from_bus"].at[0] == b1
    assert line2 in net2.line.index
    assert net2["switch"]["bus"].at[0] == b1
    assert net2["load"]["bus"].tolist() == [b1, b1, b3]
    assert net2["measurement"]["element"].at[0] == b1
    # assertion: b2 remains in net.bus table
    assert b2 in net2.bus.index
    assert b3 in net2.bus.index


def test_close_switch_at_line_with_two_open_switches():
    net = pp.create_empty_network()

    bus1 = pp.create_bus(net, vn_kv=.4)
    bus2 = pp.create_bus(net, vn_kv=.4)
    bus3 = pp.create_bus(net, vn_kv=.4)

    line1 = pp.create_line(net, bus2, bus3, length_km=1., std_type="NAYY 4x50 SE")
    line2 = pp.create_line(net, bus2, bus3, length_km=1., std_type="NAYY 4x50 SE")
    pp.create_line(net, bus2, bus3, length_km=1., std_type="NAYY 4x50 SE")  # line3

    pp.create_switch(net, bus1, bus2, et="b", closed=True)  # sw0

    pp.create_switch(net, bus2, line1, et="l", closed=False)  # sw1
    pp.create_switch(net, bus3, line1, et="l", closed=False)  # sw2

    pp.create_switch(net, bus2, line2, et="l", closed=True)  # sw3
    pp.create_switch(net, bus3, line2, et="l", closed=False)  # sw4

    pp.create_switch(net, bus3, line2, et="l", closed=True)  # sw5
    pp.create_switch(net, bus3, line2, et="l", closed=True)  # sw6

    tb.close_switch_at_line_with_two_open_switches(net)

    # assertion: sw2 closed
    assert net.switch.closed.loc[1]


def test_pq_from_cosphi():
    p, q = pp.pq_from_cosphi(1 / 0.95, 0.95, "underexcited", "load")
    assert np.isclose(p, 1)
    assert np.isclose(q, 0.3286841051788632)

    s = np.array([1, 1, 1])
    cosphi = np.array([1, 0.5, 0])
    pmode = np.array(["load", "load", "load"])
    qmode = np.array(["underexcited", "underexcited", "underexcited"])
    p, q = pp.pq_from_cosphi(s, cosphi, qmode, pmode)
    excpected_values = (np.array([1, 0.5, 0]), np.array([0, 0.8660254037844386, 1]))
    assert np.allclose(p, excpected_values[0])
    assert np.allclose(q, excpected_values[1])

    pmode = "gen"
    p, q = pp.pq_from_cosphi(s, cosphi, qmode, pmode)
    assert np.allclose(p, excpected_values[0])
    assert np.allclose(q, -excpected_values[1])

    qmode = "overexcited"
    p, q = pp.pq_from_cosphi(s, cosphi, qmode, pmode)
    assert np.allclose(p, excpected_values[0])
    assert np.allclose(q, excpected_values[1])

    with pytest.raises(ValueError):
        pp.pq_from_cosphi(1, 0.95, "ohm", "gen")

    p, q = pp.pq_from_cosphi(0, 0.8, "overexcited", "gen")
    assert np.isclose(p, 0)
    assert np.isclose(q, 0)


def test_cosphi_from_pq():
    cosphi, s, qmode, pmode = pp.cosphi_from_pq(1, 0.4)
    assert np.isclose(cosphi, 0.9284766908852593)
    assert np.isclose(s, 1.077032961426901)
    assert qmode == 'underexcited'
    assert pmode == 'load'

    p = np.array([1, 1, 1, 1, 1, 0, 0, 0, -1, -1, -1])
    q = np.array([1, -1, 0, 0.5, -0.5, 1, -1, 0, 1, -1, 0])
    cosphi, s, qmode, pmode = pp.cosphi_from_pq(p, q)
    assert np.allclose(cosphi[[0, 1, 8, 9]], 2 ** 0.5 / 2)
    assert np.allclose(cosphi[[3, 4]], 0.89442719)
    assert np.allclose(cosphi[[2, 10]], 1)
    assert pd.Series(cosphi[[5, 6, 7]]).isnull().all()
    assert np.allclose(s, (p ** 2 + q ** 2) ** 0.5)
    assert all(pmode == np.array(["load"] * 5 + ["undef"] * 3 + ["gen"] * 3))
    ind_cap_ind = ["underexcited", "overexcited", "underexcited"]
    assert all(qmode == np.array(ind_cap_ind + ["underexcited", "overexcited"] + ind_cap_ind * 2))


def test_create_replacement_switch_for_branch():
    net = pp.create_empty_network()

    bus0 = pp.create_bus(net, vn_kv=0.4)
    bus1 = pp.create_bus(net, vn_kv=0.4)
    bus2 = pp.create_bus(net, vn_kv=0.4)
    bus3 = pp.create_bus(net, vn_kv=0.4)

    pp.create_ext_grid(net, bus0, vm_pu=0.4)

    line0 = pp.create_line(net, bus0, bus1, length_km=1, std_type="NAYY 4x50 SE")
    line1 = pp.create_line(net, bus2, bus3, length_km=1, std_type="NAYY 4x50 SE")
    impedance0 = pp.create_impedance(net, bus1, bus2, 0.01, 0.01, sn_mva=100)
    impedance1 = pp.create_impedance(net, bus1, bus2, 0.01, 0.01, sn_mva=100)

    pp.create_load(net, bus2, 0.001)

    pp.runpp(net)

    # look that the switch is created properly
    tb.create_replacement_switch_for_branch(net, 'line', line0)
    tb.create_replacement_switch_for_branch(net, 'impedance', impedance0)
    net.line.in_service.at[line0] = False
    net.impedance.in_service.at[impedance0] = False

    assert 'REPLACEMENT_line_0' in net.switch.name.values
    assert 'REPLACEMENT_impedance_0' in net.switch.name.values
    assert net.switch.closed.at[0]
    assert net.switch.closed.at[1]
    pp.runpp(net)

    # look that the switch is created with the correct closed status
    net.line.in_service.at[line1] = False
    net.impedance.in_service.at[impedance1] = False
    tb.create_replacement_switch_for_branch(net, 'line', line1)
    tb.create_replacement_switch_for_branch(net, 'impedance', impedance1)

    assert 'REPLACEMENT_line_1' in net.switch.name.values
    assert 'REPLACEMENT_impedance_1' in net.switch.name.values
    assert ~net.switch.closed.at[2]
    assert ~net.switch.closed.at[3]


@pytest.fixture
def net():
    net = pp.create_empty_network()

    bus0 = pp.create_bus(net, vn_kv=0.4)
    bus1 = pp.create_bus(net, vn_kv=0.4)
    bus2 = pp.create_bus(net, vn_kv=0.4)
    bus3 = pp.create_bus(net, vn_kv=0.4)
    bus4 = pp.create_bus(net, vn_kv=0.4)
    bus5 = pp.create_bus(net, vn_kv=0.4)
    bus6 = pp.create_bus(net, vn_kv=0.4)

    pp.create_ext_grid(net, bus0, vm_pu=0.4)

    pp.create_line(net, bus0, bus1, length_km=0, std_type="NAYY 4x50 SE")  # line0
    pp.create_line_from_parameters(net, bus2, bus3, length_km=1, r_ohm_per_km=0, x_ohm_per_km=0.1,
                                   c_nf_per_km=0, max_i_ka=1)  # line1
    pp.create_line_from_parameters(net, bus3, bus4, length_km=1, r_ohm_per_km=0, x_ohm_per_km=0,
                                   c_nf_per_km=0, max_i_ka=1)  # line2

    pp.create_impedance(net, bus1, bus2, 0.01, 0.01, sn_mva=100)  # impedance0
    pp.create_impedance(net, bus4, bus5, 0, 0, sn_mva=100)  # impedance1
    pp.create_impedance(net, bus5, bus6, 0, 0, rtf_pu=0.1, sn_mva=100)  # impedance2
    return net


def test_for_line_with_zero_length(net):
    tb.replace_zero_branches_with_switches(net, elements=('line',), zero_impedance=False)
    assert 'REPLACEMENT_line_0' in net.switch.name.values
    assert ~net.line.in_service.at[0]
    assert 'REPLACEMENT_line_2' not in net.switch.name.values


def test_drop(net):
    tb.replace_zero_branches_with_switches(net, elements=('line', 'impedance'), drop_affected=True)
    assert len(net.line) == 1
    assert len(net.impedance) == 2


def test_in_service_only(net):
    tb.replace_zero_branches_with_switches(net, elements=('line',))
    assert len(net.switch.loc[net.switch.name == 'REPLACEMENT_line_0']) == 1
    tb.replace_zero_branches_with_switches(net, elements=('line',), in_service_only=False)
    assert len(net.switch.loc[net.switch.name == 'REPLACEMENT_line_0']) == 2
    assert ~net.switch.closed.at[2]


def test_line_with_zero_impediance(net):
    # test for line with zero impedance
    tb.replace_zero_branches_with_switches(net, elements=('line',), zero_length=False)
    assert 'REPLACEMENT_line_1' not in net.switch.name.values
    assert 'REPLACEMENT_line_2' in net.switch.name.values


def test_impedance(net):
    tb.replace_zero_branches_with_switches(net, elements=('impedance',), zero_length=False,
                                           zero_impedance=True, in_service_only=True)
    assert 'REPLACEMENT_impedance_0' not in net.switch.name.values
    assert 'REPLACEMENT_impedance_1' in net.switch.name.values
    assert 'REPLACEMENT_impedance_2' not in net.switch.name.values


def test_all(net):
    tb.replace_zero_branches_with_switches(net, elements=('impedance', 'line'), zero_length=True,
                                           zero_impedance=True, in_service_only=True)
    assert 'REPLACEMENT_impedance_1' in net.switch.name.values
    assert 'REPLACEMENT_line_0' in net.switch.name.values
    assert 'REPLACEMENT_line_2' in net.switch.name.values
    assert ~net.line.in_service.at[0]
    assert net.line.in_service.at[1]
    assert ~net.line.in_service.at[2]
    assert 'REPLACEMENT_impedance_0' not in net.switch.name.values
    assert 'REPLACEMENT_impedance_2' not in net.switch.name.values
    assert 'REPLACEMENT_line_1' not in net.switch.name.values
    assert net.impedance.in_service.at[0]
    assert ~net.impedance.in_service.at[1]
    assert net.impedance.in_service.at[2]


def test_get_element_indices():
    net = nw.example_multivoltage()
    idx1 = pp.get_element_indices(net, "bus", ["Bus HV%i" % i for i in range(1, 4)])
    idx2 = pp.get_element_indices(net, ["bus", "line"], "HV", exact_match=False)
    idx3 = pp.get_element_indices(net, ["bus", "line"], ["Bus HV3", "MV Line6"])
    assert [32, 33, 34] == idx1
    assert ([32, 33, 34, 35] == idx2[0]).all()
    assert ([0, 1, 2, 3, 4, 5] == idx2[1]).all()
    assert [34, 11] == idx3


def test_next_bus():
    net = pp.create_empty_network()

    bus0 = pp.create_bus(net, vn_kv=110)
    bus1 = pp.create_bus(net, vn_kv=20)
    bus2 = pp.create_bus(net, vn_kv=10)
    bus3 = pp.create_bus(net, vn_kv=0.4)
    bus4 = pp.create_bus(net, vn_kv=0.4)
    bus5 = pp.create_bus(net, vn_kv=20)

    trafo0 = pp.create_transformer3w(net, hv_bus=bus0, mv_bus=bus1, lv_bus=bus2, name='trafo0',
                                     std_type='63/25/38 MVA 110/20/10 kV')
    trafo1 = pp.create_transformer(net, hv_bus=bus2, lv_bus=bus3, std_type='0.4 MVA 10/0.4 kV')

    line1 = pp.create_line(net, from_bus=bus3, to_bus=bus4, length_km=20.1,
                           std_type='24-AL1/4-ST1A 0.4', name='line1')

    # switch0=pp.create_switch(net, bus = bus0, element = trafo0, et = 't3') #~~~~~ not implementable now
    switch1 = pp.create_switch(net, bus=bus1, element=bus5, et='b')
    switch2 = pp.create_switch(net, bus=bus2, element=trafo1, et='t')
    switch3 = pp.create_switch(net, bus=bus3, element=line1, et='l')

    # assert tb.next_bus(net,bus0,trafo0,et='trafo3w')==bus1                         # not implemented in existing toolbox
    # assert tb.next_bus(net,bus0,trafo0,et='trafo3w',choice_for_trafo3w='lv')==bus2 # not implemented in existing toolbox
    assert tb.next_bus(net, bus1, switch1, et='switch') == bus5  # Switch with bus2bus connection
    # assert not tb.next_bus(net,bus2,switch2,et='switch')==bus3  # Switch with bus2trafo connection:- gives trasformer id instead of bus id
    assert tb.next_bus(net, bus2, trafo1, et='trafo') == bus3
    # assert tb.next_bus(net,bus3,switch3,et='switch') ==bus4  # Switch with bus2line connection :- gives line id instead of bus id
    assert tb.next_bus(net, bus3, line1, et='line') == bus4


def test_get_connected_buses():
    net = pp.create_empty_network()

    bus0 = pp.create_bus(net, vn_kv=110)
    bus1 = pp.create_bus(net, vn_kv=20)
    bus2 = pp.create_bus(net, vn_kv=10)
    bus3 = pp.create_bus(net, vn_kv=0.4)
    bus4 = pp.create_bus(net, vn_kv=0.4)
    bus5 = pp.create_bus(net, vn_kv=20)

    trafo0 = pp.create_transformer3w(net, hv_bus=bus0, mv_bus=bus1, lv_bus=bus2,
                                     std_type='63/25/38 MVA 110/20/10 kV')
    trafo1 = pp.create_transformer(net, hv_bus=bus2, lv_bus=bus3, std_type='0.4 MVA 10/0.4 kV')
    line1 = pp.create_line(net, from_bus=bus3, to_bus=bus4, length_km=20.1,
                           std_type='24-AL1/4-ST1A 0.4')

    switch0a = pp.create_switch(net, bus=bus0, element=trafo0, et='t3')
    switch0b = pp.create_switch(net, bus=bus2, element=trafo0, et='t3')
    switch1 = pp.create_switch(net, bus=bus1, element=bus5, et='b')
    switch2 = pp.create_switch(net, bus=bus2, element=trafo1, et='t')
    switch3 = pp.create_switch(net, bus=bus3, element=line1, et='l')

    assert list(tb.get_connected_buses(net, [bus0])) == [bus1, bus2]
    assert list(tb.get_connected_buses(net, [bus1])) == [bus0, bus2, bus5]
    assert list(tb.get_connected_buses(net, [bus2])) == [bus0, bus1, bus3]
    assert list(tb.get_connected_buses(net, [bus3])) == [bus2, bus4]
    assert list(tb.get_connected_buses(net, [bus4])) == [bus3]
    assert list(tb.get_connected_buses(net, [bus5])) == [bus1]
    assert list(tb.get_connected_buses(net, [bus0, bus1])) == [bus2, bus5]
    assert list(tb.get_connected_buses(net, [bus2, bus3])) == [bus0, bus1, bus4]

    net.switch.loc[[switch0b, switch1, switch2, switch3], 'closed'] = False
    assert list(tb.get_connected_buses(net, [bus0])) == [bus1]
    assert list(tb.get_connected_buses(net, [bus1])) == [bus0]
    assert list(tb.get_connected_buses(net, [bus3])) == []
    assert list(tb.get_connected_buses(net, [bus4])) == []


def test_drop_elements_at_buses():
    net = pp.create_empty_network()

    bus0 = pp.create_bus(net, vn_kv=110)
    bus1 = pp.create_bus(net, vn_kv=20)
    bus2 = pp.create_bus(net, vn_kv=10)
    bus3 = pp.create_bus(net, vn_kv=0.4)
    bus4 = pp.create_bus(net, vn_kv=0.4)
    bus5 = pp.create_bus(net, vn_kv=20)

    pp.create_ext_grid(net, 0)

    trafo0 = pp.create_transformer3w(net, hv_bus=bus0, mv_bus=bus1, lv_bus=bus2, name='trafo0',
                                     std_type='63/25/38 MVA 110/20/10 kV')
    trafo1 = pp.create_transformer(net, hv_bus=bus2, lv_bus=bus3, std_type='0.4 MVA 10/0.4 kV')

    line1 = pp.create_line(net, from_bus=bus3, to_bus=bus4, length_km=20.1,
                           std_type='24-AL1/4-ST1A 0.4', name='line1')
    pp.create_sgen(net, 1, 0)

    switch0a = pp.create_switch(net, bus=bus0, element=trafo0, et='t3')
    switch0b = pp.create_switch(net, bus=bus1, element=trafo0, et='t3')
    switch0c = pp.create_switch(net, bus=bus2, element=trafo0, et='t3')
    switch1 = pp.create_switch(net, bus=bus1, element=bus5, et='b')
    switch2a = pp.create_switch(net, bus=bus2, element=trafo1, et='t')
    switch2b = pp.create_switch(net, bus=bus3, element=trafo1, et='t')
    switch3a = pp.create_switch(net, bus=bus3, element=line1, et='l')
    switch3b = pp.create_switch(net, bus=bus4, element=line1, et='l')
    # bus id needs to be entered as iterable, not done in the function

    for b in net.bus.index.values:
        net1 = net.deepcopy()
        cd = tb.get_connected_elements_dict(net1, b, connected_buses=False)
        swt3w = set(net1.switch.loc[net1.switch.element.isin(cd.get('trafo3w', [1000])) &
                                    (net1.switch.et == 't3')].index)
        swt = set(net1.switch.loc[net1.switch.element.isin(cd.get('trafo', [1000])) &
                                  (net1.switch.et == 't')].index)
        swl = set(net1.switch.loc[net1.switch.element.isin(cd.get('line', [1000])) &
                                  (net1.switch.et == 'l')].index)
        sw = swt3w | swt | swl
        tb.drop_elements_at_buses(net1, [b])
        assert b not in net1.switch.bus.values
        assert b not in net1.switch.query("et=='b'").element.values
        assert sw.isdisjoint(set(net1.switch.index))
        for elm, id in cd.items():
            assert len(net1[elm].loc[net1[elm].index.isin(id)]) == 0


def test_impedance_line_replacement():
    # create test net
    net1 = pp.create_empty_network(sn_mva=1.1)
    pp.create_buses(net1, 2, 10)
    pp.create_ext_grid(net1, 0)
    pp.create_impedance(net1, 0, 1, 0.1, 0.1, 8.7e-3)
    pp.create_load(net1, 1, 7e-3, 2e-3)

    # validate loadflow results
    pp.runpp(net1)

    net2 = copy.deepcopy(net1)
    pp.replace_impedance_by_line(net2)

    pp.runpp(net2)

    assert pp.nets_equal(net1, net2, exclude_elms={"line", "impedance"})
    cols = ["p_from_mw", "q_from_mvar", "p_to_mw", "q_to_mvar", "pl_mw", "ql_mvar", "i_from_ka",
            "i_to_ka"]
    assert np.allclose(net1.res_impedance[cols].values, net2.res_line[cols].values)

    net3 = copy.deepcopy(net2)
    pp.replace_line_by_impedance(net3)

    pp.runpp(net3)

    assert pp.nets_equal(net2, net3, exclude_elms={"line", "impedance"})
    assert np.allclose(net3.res_impedance[cols].values, net2.res_line[cols].values)


def test_replace_ext_grid_gen():
    for i in range(2):
        net = nw.example_simple()
        net.ext_grid["uuid"] = "test"
        pp.runpp(net)
        assert list(net.res_ext_grid.index.values) == [0]

        # replace_ext_grid_by_gen
        if i == 0:
            pp.replace_ext_grid_by_gen(net, 0, gen_indices=[4], add_cols_to_keep=["uuid"])
        elif i == 1:
            pp.replace_ext_grid_by_gen(net, [0], gen_indices=[4], cols_to_keep=["uuid", "max_p_mw"])
        assert not net.ext_grid.shape[0]
        assert not net.res_ext_grid.shape[0]
        assert np.allclose(net.gen.vm_pu.values, [1.03, 1.02])
        assert net.res_gen.p_mw.dropna().shape[0] == 2
        assert np.allclose(net.gen.index.values, [0, 4])
        assert net.gen.uuid.loc[4] == "test"

        # replace_gen_by_ext_grid
        if i == 0:
            pp.replace_gen_by_ext_grid(net)
        elif i == 1:
            pp.replace_gen_by_ext_grid(net, [0, 4], ext_grid_indices=[2, 3])
            assert np.allclose(net.ext_grid.index.values, [2, 3])
        assert not net.gen.shape[0]
        assert not net.res_gen.shape[0]
        assert net.ext_grid.va_degree.dropna().shape[0] == 2
        assert any(np.isclose(net.ext_grid.va_degree.values, 0))
        assert net.res_ext_grid.p_mw.dropna().shape[0] == 2


def test_replace_gen_sgen():
    for i in range(2):
        net = nw.case9()
        vm_set = [1.03, 1.02]
        net.gen["vm_pu"] = vm_set
        net.gen["slack_weight"] = 1
        pp.runpp(net)
        assert list(net.res_gen.index.values) == [0, 1]

        # replace_gen_by_sgen
        if i == 0:
            pp.replace_gen_by_sgen(net)
        elif i == 1:
            pp.replace_gen_by_sgen(net, [0, 1], sgen_indices=[4, 1], cols_to_keep=[
                "max_p_mw"], add_cols_to_keep=["slack_weight"])  # min_p_mw is not in cols_to_keep
            assert np.allclose(net.sgen.index.values, [4, 1])
            assert np.allclose(net.sgen.slack_weight.values, 1)
            assert "max_p_mw" in net.sgen.columns
            assert "min_p_mw" not in net.sgen.columns
        assert not net.gen.shape[0]
        assert not net.res_gen.shape[0]
        assert not np.allclose(net.sgen.q_mvar.values, 0)
        assert net.res_gen.shape[0] == 0
        pp.runpp(net)
        assert np.allclose(net.res_bus.loc[net.sgen.bus, "vm_pu"].values, vm_set)

        # replace_sgen_by_gen
        net2 = copy.deepcopy(net)
        if i == 0:
            pp.replace_sgen_by_gen(net2, [1])
        elif i == 1:
            pp.replace_sgen_by_gen(net2, 1, gen_indices=[2], add_cols_to_keep=["slack_weight"])
            assert np.allclose(net2.gen.index.values, [2])
            assert np.allclose(net2.gen.slack_weight.values, 1)
        assert net2.gen.shape[0] == 1
        assert net2.res_gen.shape[0] == 1
        assert net2.gen.shape[0] == 1
        assert net2.res_gen.shape[0] == 1

        if i == 0:
            pp.replace_sgen_by_gen(net, 1)
            assert pp.nets_equal(net, net2)


def test_replace_pq_elmtype():
    def check_elm_shape(net, elm_shape: dict):
        for elm, no in elm_shape.items():
            assert net[elm].shape[0] == no

    net = pp.create_empty_network()
    pp.create_buses(net, 3, 20)
    pp.create_ext_grid(net, 0)
    for to_bus in [1, 2]:
        pp.create_line(net, 0, to_bus, 0.6, 'NA2XS2Y 1x95 RM/25 12/20 kV')
    names = ["load 1", "load 2"]
    types = ["house", "commercial"]
    pp.create_loads(net, [1, 2], 0.8, 0.1, sn_mva=1, min_p_mw=0.5, max_p_mw=1.0, controllable=True,
                    name=names, scaling=[0.8, 1], type=types)
    pp.create_poly_cost(net, 0, "load", 7)
    pp.create_poly_cost(net, 1, "load", 3)
    pp.runpp(net)
    net.load["controllable"] = net.load["controllable"].astype(bool)
    net_orig = copy.deepcopy(net)

    # --- test unset old_indices, cols_to_keep and add_cols_to_keep
    pp.replace_pq_elmtype(net, "load", "sgen", new_indices=[2, 7], cols_to_keep=["type"],
                          add_cols_to_keep=["scaling"])  # cols_to_keep is not
    # default but ["type"] -> min/max p_mw get lost
    check_elm_shape(net, {"load": 0, "sgen": 2})
    assert list(net.sgen.index) == [2, 7]
    assert list(net.sgen.type.values) == types
    assert list(net.sgen.name.values) == names
    assert net.sgen.controllable.astype(bool).all()
    assert "min_p_mw" not in net.sgen.columns
    pp.runpp(net)
    assert pp.dataframes_equal(net_orig.res_bus, net.res_bus)

    # --- test set old_indices and add_cols_to_keep for different element types
    net = copy.deepcopy(net_orig)
    add_cols_to_keep = ["scaling", "type", "sn_mva"]
    pp.replace_pq_elmtype(net, "load", "sgen", old_indices=1, add_cols_to_keep=add_cols_to_keep)
    check_elm_shape(net, {"load": 1, "sgen": 1})
    pp.runpp(net)
    assert pp.dataframes_equal(net_orig.res_bus, net.res_bus)
    assert net.sgen.max_p_mw.at[0] == - 0.5
    assert net.sgen.min_p_mw.at[0] == - 1.0

    pp.replace_pq_elmtype(net, "sgen", "storage", old_indices=0, add_cols_to_keep=add_cols_to_keep)
    check_elm_shape(net, {"load": 1, "storage": 1})
    pp.runpp(net)
    assert pp.dataframes_equal(net_orig.res_bus, net.res_bus)

    pp.replace_pq_elmtype(net, "storage", "load", add_cols_to_keep=add_cols_to_keep)
    pp.runpp(net)
    check_elm_shape(net, {"storage": 0, "sgen": 0})
    net.poly_cost.element = net.poly_cost.element.astype(net_orig.poly_cost.dtypes["element"])
    assert pp.nets_equal(net_orig, net, exclude_elms={"sgen", "storage"})


def test_get_connected_elements_dict():
    net = nw.example_simple()
    conn = pp.get_connected_elements_dict(net, [0])
    assert conn == {"line": [0], 'ext_grid': [0], 'bus': [1]}
    conn = pp.get_connected_elements_dict(net, [3, 4])
    assert conn == {'line': [1, 3], 'switch': [1, 2, 7], 'trafo': [0], 'bus': [2, 5, 6]}


def test_get_connected_elements_empty_in_service():
    # would cause an error with respect_in_service=True for the case of:
    #  - empty element tables
    #  - element tables without in_service column (e.g. measurement)
    #  - element_table was unbound for the element table measurement
    #  see #1592
    net = nw.example_simple()
    net.bus.in_service.at[6] = False
    conn = pp.get_connected_elements_dict(net, [0], respect_switches=False, respect_in_service=True)
    assert conn == {"line": [0], 'ext_grid': [0], 'bus': [1]}
    conn = pp.get_connected_elements_dict(net, [3, 4], respect_switches=False, respect_in_service=True)
    assert conn == {'line': [1, 3], 'switch': [1, 2, 7], 'trafo': [0], 'bus': [2, 5]}


def test_replace_ward_by_internal_elements():
    net = nw.example_simple()
    pp.create_ward(net, 1, 10, 5, -20, -10, name="ward_1")
    pp.create_ward(net, 5, 6, 8, 10, 5, name="ward_2")
    pp.create_ward(net, 6, -1, 9, 11, 6, name="ward_3", in_service=False)
    pp.runpp(net)
    net_org = copy.deepcopy(net)
    pp.replace_ward_by_internal_elements(net)
    for elm in ["load", "shunt"]:
        assert net[elm].shape[0] == 4
    res_load_created, res_shunt_created = copy.deepcopy(net.res_load), copy.deepcopy(net.res_shunt)
    pp.runpp(net)
    assert np.allclose(net_org.res_ext_grid.p_mw, net.res_ext_grid.p_mw)
    assert np.allclose(net_org.res_ext_grid.q_mvar, net.res_ext_grid.q_mvar)
    assert np.allclose(res_load_created, net.res_load)
    assert np.allclose(res_shunt_created, net.res_shunt)

    net = nw.example_simple()
    pp.create_ward(net, 1, 10, 5, -20, -10, name="ward_1")
    pp.create_ward(net, 5, 6, 8, 10, 5, name="ward_2")
    pp.create_ward(net, 6, -1, 9, 11, 6, name="ward_3", in_service=False)
    pp.runpp(net)
    net_org = copy.deepcopy(net)
    pp.replace_ward_by_internal_elements(net, [1])
    for elm in ["load", "shunt"]:
        assert net[elm].shape[0] == 2
    res_load_created, res_shunt_created = copy.deepcopy(net.res_load), copy.deepcopy(net.res_shunt)
    pp.runpp(net)
    assert np.allclose(net_org.res_ext_grid.p_mw, net.res_ext_grid.p_mw)
    assert np.allclose(net_org.res_ext_grid.q_mvar, net.res_ext_grid.q_mvar)
    assert np.allclose(res_load_created, net.res_load)
    assert np.allclose(res_shunt_created, net.res_shunt)


def test_replace_xward_by_internal_elements():
    net = nw.example_simple()
    pp.create_xward(net, 1, 10, 5, -20, -10, 0.1, 0.55, 1.02, name="xward_1")
    pp.create_xward(net, 5, 6, 8, 10, 5, 0.009, 0.678, 1.03, name="xward_2")
    pp.create_xward(net, 6, 6, 8, 10, 5, 0.009, 0.678, 1.03, in_service=False, name="xward_3")
    pp.runpp(net)
    net_org = copy.deepcopy(net)
    pp.replace_xward_by_internal_elements(net)
    pp.runpp(net)
    assert abs(max(net_org.res_ext_grid.p_mw - net.res_ext_grid.p_mw)) < 1e-10
    assert abs(max(net_org.res_ext_grid.q_mvar - net.res_ext_grid.q_mvar)) < 1e-10

    net = nw.example_simple()
    pp.create_xward(net, 1, 10, 5, -20, -10, 0.1, 0.55, 1.02, name="xward_1")
    pp.create_xward(net, 5, 6, 8, 10, 5, 0.009, 0.678, 1.03, name="xward_2")
    pp.create_xward(net, 6, 6, 8, 10, 5, 0.009, 0.678, 1.03, in_service=False, name="xward_3")
    pp.runpp(net)
    net_org = copy.deepcopy(net)
    pp.replace_xward_by_internal_elements(net, [0, 1])
    pp.runpp(net)
    assert abs(max(net_org.res_ext_grid.p_mw - net.res_ext_grid.p_mw)) < 1e-10
    assert abs(max(net_org.res_ext_grid.q_mvar - net.res_ext_grid.q_mvar)) < 1e-10


def test_repl_to_line():
    net = nw.simple_four_bus_system()
    idx = 0
    std_type = "NAYY 4x150 SE"
    new_idx = tb.repl_to_line(net, idx, std_type, in_service=True)
    pp.runpp(net)

    vm1 = net.res_bus.vm_pu.values
    va1 = net.res_bus.va_degree.values

    net.line.at[new_idx, "in_service"] = False
    pp.change_std_type(net, idx, std_type)
    pp.runpp(net)

    vm0 = net.res_bus.vm_pu.values
    va0 = net.res_bus.va_degree.values

    assert np.allclose(vm1, vm0)
    assert np.allclose(va1, va0)


def test_repl_to_line_with_switch():
    """
    Same test as above, but this time in comparison to actual replacement
    """
    net = nw.example_multivoltage()
    pp.runpp(net)

    for testindex in net.line.index:
        if net.line.in_service.loc[testindex]:
            line = net.line.loc[testindex]
            fbus = line.from_bus
            tbus = line.to_bus
            len = line.length_km

            if "184-AL1/30-ST1A" in net.line.std_type.loc[testindex]:
                std = "243-AL1/39-ST1A 110.0"
            elif "NA2XS2Y" in net.line.std_type.loc[testindex]:
                std = "NA2XS2Y 1x240 RM/25 6/10 kV"
            elif "NAYY" in net.line.std_type.loc[testindex]:
                std = "NAYY 4x150 SE"
            elif " 15-AL1/3-ST1A" in net.line.std_type.loc[testindex]:
                std = "24-AL1/4-ST1A 0.4"

            # create an oos line at the same buses
            REPL = pp.create_line(net, from_bus=fbus, to_bus=tbus, length_km=len, std_type=std)

            for bus in fbus, tbus:
                if bus in net.switch[~net.switch.closed & (net.switch.element == testindex)].bus.values:
                    pp.create_switch(net, bus=bus, element=REPL, closed=False, et="l", type="LBS")

            # calculate runpp with REPL
            net.line.in_service[testindex] = False
            net.line.in_service[REPL] = True
            pp.runpp(net)

            fbus_repl = net.res_bus.loc[fbus]
            tbus_repl = net.res_bus.loc[tbus]

            ploss_repl = (net.res_line.loc[REPL].p_from_mw - net.res_line.loc[REPL].p_to_mw)
            qloss_repl = (net.res_line.loc[REPL].q_from_mvar - net.res_line.loc[REPL].q_to_mvar)

            # get ne line impedances
            new_idx = tb.repl_to_line(net, testindex, std, in_service=True)
            # activate new idx line
            net.line.in_service[REPL] = False
            net.line.in_service[testindex] = True
            net.line.in_service[new_idx] = True
            pp.runpp(net)
            # compare lf results
            fbus_ne = net.res_bus.loc[fbus]
            tbus_ne = net.res_bus.loc[tbus]
            ploss_ne = (net.res_line.loc[testindex].p_from_mw -
                        net.res_line.loc[testindex].p_to_mw) + \
                       (net.res_line.loc[new_idx].p_from_mw - net.res_line.loc[new_idx].p_to_mw)
            qloss_ne = (net.res_line.loc[testindex].q_from_mvar -
                        net.res_line.loc[testindex].q_to_mvar) + \
                       (net.res_line.loc[new_idx].q_from_mvar - net.res_line.loc[new_idx].q_to_mvar)

            assert_series_equal(fbus_repl, fbus_ne, atol=1e-2)
            assert_series_equal(tbus_repl, tbus_ne)
            assert np.isclose(ploss_repl, ploss_ne, atol=1e-5)
            assert np.isclose(qloss_repl, qloss_ne)

            # and reset to unreinforced state again
            net.line.in_service[testindex] = True
            net.line.in_service[new_idx] = False
            net.line.in_service[REPL] = False


def test_merge_parallel_line():
    net = nw.example_multivoltage()
    pp.runpp(net)
    assert net.line.parallel.at[5] == 2

    line = net.line.loc[5]
    fbus = line.from_bus
    tbus = line.to_bus

    fbus_0 = net.res_bus.loc[fbus]
    tbus_0 = net.res_bus.loc[tbus]
    ploss_0 = (net.res_line.loc[5].p_from_mw - net.res_line.loc[5].p_to_mw)
    qloss_0 = (net.res_line.loc[5].q_from_mvar - net.res_line.loc[5].q_to_mvar)

    net = tb.merge_parallel_line(net, 5)

    assert net.line.parallel.at[5] == 1
    pp.runpp(net)
    fbus_1 = net.res_bus.loc[fbus]
    tbus_1 = net.res_bus.loc[tbus]
    ploss_1 = (net.res_line.loc[5].p_from_mw - net.res_line.loc[5].p_to_mw)
    qloss_1 = (net.res_line.loc[5].q_from_mvar - net.res_line.loc[5].q_to_mvar)

    assert_series_equal(fbus_0, fbus_1)
    assert_series_equal(tbus_0, tbus_1)
    assert np.isclose(ploss_0, ploss_1, atol=1e-5)
    assert np.isclose(qloss_0, qloss_1)


def test_merge_same_bus_generation_plants():
    gen_elms = ["ext_grid", "gen", "sgen"]

    # --- test with case9
    net = nw.case9()
    buses = np.hstack([net[elm].bus.values for elm in gen_elms])
    has_dupls = len(buses) > len(set(buses))

    something_merged = tb.merge_same_bus_generation_plants(net)

    assert has_dupls == something_merged

    # --- test with case24_ieee_rts
    net = nw.case24_ieee_rts()

    # manipulate net for different functionality checks
    # 1) q_mvar should be summed which is only possible if no gen or ext_grid has the same bus
    net.gen.drop(net.gen.index[net.gen.bus == 22], inplace=True)
    net.sgen["q_mvar"] = np.arange(net.sgen.shape[0])
    # 2) remove limit columns or values to check whether merge_same_bus_generation_plants() can
    # handle that
    del net.sgen["max_q_mvar"]
    net.sgen.min_p_mw.at[1] = np.nan

    # prepare expatation values
    dupl_buses = [0, 1, 6, 12, 14, 21, 22]
    n_plants = sum([net[elm].bus.isin(dupl_buses).sum() for elm in gen_elms])
    assert n_plants > len(dupl_buses)  # check that in net are plants with same buses
    expected_no_of_plants = sum([net[elm].shape[0] for elm in gen_elms]) - n_plants + \
                            len(dupl_buses)

    # run function
    something_merged = tb.merge_same_bus_generation_plants(net)

    # check results
    assert something_merged
    buses = np.hstack([net[elm].bus.values for elm in gen_elms])
    assert len(buses) == len(set(buses))  # no dupl buses in gen plant dfs
    n_plants = sum([net[elm].shape[0] for elm in gen_elms])
    assert n_plants == expected_no_of_plants
    assert np.isclose(net.ext_grid.p_disp_mw.at[0], 95.1 * 2)  # correct value sum (p_disp)
    assert np.isclose(net.gen.p_mw.at[0], 10 * 2 + 76 * 2)  # correct value sum (p_mw)
    assert np.isclose(net.gen.min_p_mw.at[0], 16 * 2 + 15.2)  # correct value sum (min_p_mw) (
    # 1x 15.2 has been removed above)
    assert np.isclose(net.gen.max_p_mw.at[0], 20 * 2 + 76 * 2)  # correct value sum (max_p_mw)
    assert np.isclose(net.gen.min_q_mvar.at[8], -10 - 16 * 5)  # correct value sum (min_q_mvar)
    assert np.isclose(net.gen.max_q_mvar.at[8], 16)  # correct value sum (max_q_mvar) (
    # the sgen max_q_mvar column has been removed above)
    idx_sgen22 = net.sgen.index[net.sgen.bus == 22]
    assert len(idx_sgen22) == 1
    assert np.isclose(net.sgen.q_mvar.at[idx_sgen22[0]], 20 + 21)  # correct value sum (q_mvar)


def test_get_false_links():
    net = pp.create_empty_network()
    pp.create_buses(net, 6, 10, index=[0, 1, 3, 4, 6, 7])

    # --- gens
    pp.create_gens(net, [0, 1, 3], 5)
    # manipulate to not existing
    net.gen.bus.at[1] = 999

    # --- sgens
    pp.create_sgens(net, [0, 1, 3], 5)

    # --- lines
    for fbus, tbus in zip([0, 1, 4, 6, 7], [1, 4, 6, 7, 3]):
        pp.create_line(net, fbus, tbus, 2., "NA2XS2Y 1x185 RM/25 6/10 kV")
    # manipulate to not existing
    net.line.from_bus.at[1] = 2
    net.line.to_bus.at[4] = 999

    # --- measurements
    pp.create_measurement(net, "v", "bus", 1.01, 5, 1)
    pp.create_measurement(net, "i", "line", 0.41, 1, 0, side="from")
    pp.create_measurement(net, "i", "line", 0.41, 1, 2, side="from")
    pp.create_measurement(net, "v", "bus", 1.01, 5, 6)
    pp.create_measurement(net, "i", "line", 0.41, 1, 1, side="from")
    # manipulate to not existing
    net.measurement.element.at[1] = 999
    net.measurement.element.at[3] = 999

    # --- poly_cost
    pp.create_poly_cost(net, 0, "gen", 5)
    pp.create_poly_cost(net, 1, "gen", 5)
    pp.create_poly_cost(net, 0, "sgen", 5)
    pp.create_poly_cost(net, 1, "sgen", 5)
    # manipulate to not existing
    net.poly_cost.element.at[1] = 999
    net.poly_cost.element.at[2] = 999

    expected = {"gen": {1},
                "line": {1, 4},
                "measurement": {1, 3},
                "poly_cost": {1, 2}}
    determined = tb.false_elm_links_loop(net)
    assert {elm: set(idx) for elm, idx in determined.items()} == expected


if __name__ == '__main__':
    pytest.main([__file__, "-x"])
