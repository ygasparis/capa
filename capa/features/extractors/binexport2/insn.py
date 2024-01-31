# Copyright (C) 2023 Mandiant, Inc. All Rights Reserved.
# Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
# You may obtain a copy of the License at: [package root]/LICENSE.txt
# Unless required by applicable law or agreed to in writing, software distributed under the License
#  is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and limitations under the License.
from typing import Tuple, Iterator

import capa.features.extractors.helpers
from capa.features.insn import API, Number, Mnemonic, OperandNumber, Bytes
from capa.features.common import Feature, Characteristic
from capa.features.address import Address, AbsoluteVirtualAddress
from capa.features.extractors.binexport2 import FunctionContext, InstructionContext
from capa.features.extractors.base_extractor import BBHandle, InsnHandle, FunctionHandle
from capa.features.extractors.binexport2.binexport2_pb2 import BinExport2


def extract_insn_api_features(fh: FunctionHandle, _bbh: BBHandle, ih: InsnHandle) -> Iterator[Tuple[Feature, Address]]:
    from capa.features.extractors.binexport2.extractor import BinExport2Analysis

    fhi: FunctionContext = fh.inner
    ii: InstructionContext = ih.inner

    be2 = fhi.be2
    idx = fhi.idx
    analysis: BinExport2Analysis = fhi.analysis

    instruction = be2.instruction[ii.instruction_index]

    if not instruction.call_target:
        return

    for call_target_address in instruction.call_target:
        if call_target_address in analysis.thunks:
            vertex_index = analysis.thunks[call_target_address]
        elif call_target_address not in idx.vertex_index_by_address:
            continue
        else:
            vertex_index = idx.vertex_index_by_address[call_target_address]

        vertex = be2.call_graph.vertex[vertex_index]
        if not vertex.HasField("mangled_name"):
            continue

        yield API(vertex.mangled_name), ih.address

        if vertex.HasField("library_index"):
            # BUG: this seems to be incorrect
            library = be2.library[vertex.library_index]
            library_name = library.name
            if library_name.endswith(".so"):
                library_name = library_name.rpartition(".so")[0]
            yield API(f"{library_name}.{vertex.mangled_name}"), ih.address


def is_address_mapped(be2: BinExport2, address: int) -> bool:
    """return True if the given address is mapped"""
    for section in be2.section:
        if section.address <= address < section.address + section.size:
            return True
    return False


def extract_insn_number_features(
    fh: FunctionHandle, _bbh: BBHandle, ih: InsnHandle
) -> Iterator[Tuple[Feature, Address]]:
    fhi: FunctionContext = fh.inner
    ii: InstructionContext = ih.inner

    be2 = fhi.be2

    instruction = be2.instruction[ii.instruction_index]

    for i, operand_index in enumerate(instruction.operand_index):
        operand = be2.operand[operand_index]

        if len(operand.expression_index) != 2:
            # we only care about immediate constants,
            # which have a two expression node:
            #
            # - type: SIZE_PREFIX
            #   symbol: "b8"
            # - type: IMMEDIATE_INT
            #   immediate: 20588728364
            #   parent_index: 0
            continue

        expression0 = be2.expression[operand.expression_index[0]]
        expression1 = be2.expression[operand.expression_index[1]]

        if BinExport2.Expression.Type.SIZE_PREFIX != expression0.type:
            continue

        if BinExport2.Expression.Type.IMMEDIATE_INT != expression1.type:
            continue

        value = expression1.immediate

        # TODO: skip small numbers?

        if is_address_mapped(be2, value):
            continue

        yield Number(value), ih.address
        yield OperandNumber(i, value), ih.address


def extract_insn_bytes_features(fh: FunctionHandle, bbh: BBHandle, ih: InsnHandle) -> Iterator[Tuple[Feature, Address]]:
    fhi: FunctionContext = fh.inner
    ii: InstructionContext = ih.inner

    be2 = fhi.be2
    idx = fhi.idx

    instruction_index = ii.instruction_index

    if instruction_index in idx.data_reference_index_by_source_instruction_index:
        for data_reference_index in idx.data_reference_index_by_source_instruction_index[
            instruction_index
        ]:
            data_reference = be2.data_reference[data_reference_index]
            data_reference_address = data_reference.address

            # TODO: read data
            buf = b""

            if capa.features.extractors.helpers.all_zeros(buf):
                continue

            if is_probably_string(buf):
                pass
            else:
                yield Bytes(buf), ih.address


def extract_insn_string_features(
    fh: FunctionHandle, bbh: BBHandle, ih: InsnHandle
) -> Iterator[Tuple[Feature, Address]]:
    # TODO(wb): 1755
    yield from ()


def extract_insn_offset_features(
    fh: FunctionHandle, bbh: BBHandle, ih: InsnHandle
) -> Iterator[Tuple[Feature, Address]]:
    # TODO(wb): 1755
    yield from ()


def extract_insn_nzxor_characteristic_features(
    fh: FunctionHandle, bbh: BBHandle, ih: InsnHandle
) -> Iterator[Tuple[Feature, Address]]:
    # TODO(wb): 1755
    yield from ()


def extract_insn_mnemonic_features(
    fh: FunctionHandle, bbh: BBHandle, ih: InsnHandle
) -> Iterator[Tuple[Feature, Address]]:
    fhi: FunctionContext = fh.inner
    ii: InstructionContext = ih.inner

    be2 = fhi.be2

    instruction = be2.instruction[ii.instruction_index]
    mnemonic = be2.mnemonic[instruction.mnemonic_index]
    mnemonic_name = mnemonic.name.lower()
    yield Mnemonic(mnemonic_name), ih.address


def extract_function_calls_from(fh: FunctionHandle, bbh: BBHandle, ih: InsnHandle) -> Iterator[Tuple[Feature, Address]]:
    """extract functions calls from features

    most relevant at the function scope;
    however, its most efficient to extract at the instruction scope.
    """
    fhi: FunctionContext = fh.inner
    ii: InstructionContext = ih.inner

    be2 = fhi.be2

    instruction = be2.instruction[ii.instruction_index]
    if not instruction.call_target:
        return

    for call_target_address in instruction.call_target:
        addr = AbsoluteVirtualAddress(call_target_address)
        yield Characteristic("calls from"), addr

        if fh.address == addr:
            yield Characteristic("recursive call"), addr


def extract_function_indirect_call_characteristic_features(
    fh: FunctionHandle, bbh: BBHandle, ih: InsnHandle
) -> Iterator[Tuple[Feature, Address]]:
    # TODO(wb): 1755
    yield from ()


def extract_features(f: FunctionHandle, bbh: BBHandle, insn: InsnHandle) -> Iterator[Tuple[Feature, Address]]:
    """extract instruction features"""
    for inst_handler in INSTRUCTION_HANDLERS:
        for feature, ea in inst_handler(f, bbh, insn):
            yield feature, ea


INSTRUCTION_HANDLERS = (
    extract_insn_api_features,
    extract_insn_number_features,
    extract_insn_bytes_features,
    extract_insn_string_features,
    extract_insn_offset_features,
    extract_insn_nzxor_characteristic_features,
    extract_insn_mnemonic_features,
    extract_function_calls_from,
    extract_function_indirect_call_characteristic_features,
)