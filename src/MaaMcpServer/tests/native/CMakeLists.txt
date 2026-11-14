add_executable(MaaMcpPauseEvidenceTest test_pause_evidence.cpp)
target_include_directories(MaaMcpPauseEvidenceTest PRIVATE ${PROJECT_SOURCE_DIR}/src/MaaCore)
target_link_libraries(MaaMcpPauseEvidenceTest PRIVATE HeaderOnlyLibraries)
add_test(NAME MaaMcpPauseEvidenceTest COMMAND MaaMcpPauseEvidenceTest)

add_executable(MaaMcpResourceLoadingTest
    test_resource_loading.cpp
    ${PROJECT_SOURCE_DIR}/src/MaaCore/Config/TemplResource.cpp
    ${PROJECT_SOURCE_DIR}/src/MaaCore/Config/Miscellaneous/BattleDataConfig.cpp
    ${PROJECT_SOURCE_DIR}/src/MaaCore/Config/AbstractConfig.cpp)
target_include_directories(MaaMcpResourceLoadingTest PRIVATE ${PROJECT_SOURCE_DIR}/src/MaaCore)
target_link_libraries(MaaMcpResourceLoadingTest PRIVATE HeaderOnlyLibraries MaaUtils ${OpenCV_LIBS})
if(WIN32)
    target_sources(MaaMcpResourceLoadingTest PRIVATE
        ${PROJECT_SOURCE_DIR}/src/MaaCore/Utils/Platform/PlatformWin32.cpp)
    target_compile_definitions(MaaMcpResourceLoadingTest PRIVATE _UNICODE UNICODE)
endif()
add_test(NAME MaaMcpResourceLoadingTest COMMAND MaaMcpResourceLoadingTest)

add_custom_target(MaaMcpNativeTests DEPENDS MaaMcpPauseEvidenceTest MaaMcpResourceLoadingTest)

if(CMAKE_SYSTEM_NAME STREQUAL "Linux")
    add_executable(MaaMcpAdbScreencapTransportTest test_adb_screencap_transport.cpp)
    target_include_directories(MaaMcpAdbScreencapTransportTest PRIVATE ${PROJECT_SOURCE_DIR}/src/MaaCore)
    target_link_libraries(MaaMcpAdbScreencapTransportTest PRIVATE MaaCore)
    add_test(NAME MaaMcpAdbScreencapTransportTest COMMAND MaaMcpAdbScreencapTransportTest)
    add_dependencies(MaaMcpNativeTests MaaMcpAdbScreencapTransportTest)
endif()
