#
# mod-acsdk CMake integration.
#
# AzerothCore includes modules/<name>/<name>.cmake inline from modules/CMakeLists.txt
# after the `modules` target is created, so this file can add external link deps to it.
#
# Dependencies come from vcpkg (server/vcpkg.json, triplet x64-windows-static-md):
# static libraries with the dynamic CRT (/MD), matching worldserver, so nothing extra
# has to be deployed next to worldserver.exe.
#

find_package(protobuf CONFIG REQUIRED)
find_package(gRPC CONFIG REQUIRED)

# The module directory is a junction into the SDK repo (scripts/link_module.ps1), so the
# protos live at <repo>/proto. build.ps1 passes ACSDK_PROTO_DIR explicitly; the fallback
# resolves the junction.
if(NOT ACSDK_PROTO_DIR)
  file(REAL_PATH "${CMAKE_CURRENT_LIST_DIR}/../../proto" ACSDK_PROTO_DIR)
endif()
set(ACSDK_PROTO_DIR "${ACSDK_PROTO_DIR}" CACHE PATH "Directory containing acsdk/v1/*.proto")

if(NOT EXISTS "${ACSDK_PROTO_DIR}/acsdk/v1/server.proto")
  message(FATAL_ERROR "mod-acsdk: protos not found in ACSDK_PROTO_DIR='${ACSDK_PROTO_DIR}'")
endif()

file(GLOB ACSDK_PROTO_FILES "${ACSDK_PROTO_DIR}/acsdk/v1/*.proto")
set(ACSDK_PROTO_GEN_DIR "${CMAKE_CURRENT_BINARY_DIR}/acsdk-gen")
file(MAKE_DIRECTORY "${ACSDK_PROTO_GEN_DIR}")

# Generated code is built in its own library so AzerothCore's warning flags don't apply to it.
add_library(acsdk-proto STATIC ${ACSDK_PROTO_FILES})

target_link_libraries(acsdk-proto
  PUBLIC
    protobuf::libprotobuf
    gRPC::grpc++)

target_include_directories(acsdk-proto
  PUBLIC
    "${ACSDK_PROTO_GEN_DIR}")

protobuf_generate(
  TARGET acsdk-proto
  LANGUAGE cpp
  IMPORT_DIRS "${ACSDK_PROTO_DIR}"
  PROTOC_OUT_DIR "${ACSDK_PROTO_GEN_DIR}")

protobuf_generate(
  TARGET acsdk-proto
  LANGUAGE grpc
  GENERATE_EXTENSIONS .grpc.pb.h .grpc.pb.cc
  PLUGIN "protoc-gen-grpc=\$<TARGET_FILE:gRPC::grpc_cpp_plugin>"
  IMPORT_DIRS "${ACSDK_PROTO_DIR}"
  PROTOC_OUT_DIR "${ACSDK_PROTO_GEN_DIR}")

set_target_properties(acsdk-proto
  PROPERTIES
    FOLDER "modules")

target_link_libraries(modules
  PUBLIC
    acsdk-proto)

# Golden-byte tests for the packet builders/decoders (game-free code; links only AC's `shared`).
# Run: <build>/bin/<Config>/acsdk-packet-tests.exe
get_filename_component(ACSDK_REPO_DIR "${ACSDK_PROTO_DIR}/.." ABSOLUTE)
add_executable(acsdk-packet-tests
  "${ACSDK_REPO_DIR}/server/tests/PacketTests.cpp"
  "${CMAKE_CURRENT_LIST_DIR}/src/packets/Packets.cpp"
  "${CMAKE_CURRENT_LIST_DIR}/src/packets/EventPackets.cpp"
  "${CMAKE_CURRENT_LIST_DIR}/src/packets/InteractionPackets.cpp")

target_include_directories(acsdk-packet-tests
  PRIVATE
    "${CMAKE_CURRENT_LIST_DIR}/src/packets")

target_link_libraries(acsdk-packet-tests
  PRIVATE
    acore-core-interface
    shared)

set_target_properties(acsdk-packet-tests
  PROPERTIES
    FOLDER "modules"
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/bin")
