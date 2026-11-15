add_executable(MaaMcpPauseEvidenceTest test_pause_evidence.cpp)
target_include_directories(MaaMcpPauseEvidenceTest PRIVATE ${PROJECT_SOURCE_DIR}/src/MaaCore)
target_link_libraries(MaaMcpPauseEvidenceTest PRIVATE HeaderOnlyLibraries)
add_test(NAME MaaMcpPauseEvidenceTest COMMAND MaaMcpPauseEvidenceTest)

add_executable(MaaMcpBattleStateTest test_battle_state.cpp)
target_include_directories(MaaMcpBattleStateTest PRIVATE ${PROJECT_SOURCE_DIR}/src/MaaCore)
target_link_libraries(MaaMcpBattleStateTest PRIVATE HeaderOnlyLibraries ${OpenCV_LIBS})
add_test(NAME MaaMcpBattleStateTest COMMAND MaaMcpBattleStateTest)

add_executable(MaaMcpMediaTest test_media.cpp ${PROJECT_SOURCE_DIR}/src/MaaCore/Utils/BattleMedia.cpp)
target_include_directories(MaaMcpMediaTest PRIVATE ${PROJECT_SOURCE_DIR}/src/MaaCore)
target_link_libraries(MaaMcpMediaTest PRIVATE HeaderOnlyLibraries MaaUtils ${OpenCV_LIBS})
if(WIN32)
    target_sources(MaaMcpMediaTest PRIVATE ${PROJECT_SOURCE_DIR}/src/MaaCore/Utils/Platform/PlatformWin32.cpp)
    target_compile_definitions(MaaMcpMediaTest PRIVATE _UNICODE UNICODE)
endif()
add_test(NAME MaaMcpMediaTest COMMAND MaaMcpMediaTest)

add_executable(MaaMcpResourceLoadingTest
    test_resource_loading.cpp
    ${PROJECT_SOURCE_DIR}/src/MaaCore/Config/TemplResource.cpp
    ${PROJECT_SOURCE_DIR}/src/MaaCore/Config/Miscellaneous/BattleDataConfig.cpp
    ${PROJECT_SOURCE_DIR}/src/MaaCore/Config/AbstractConfig.cpp
    ${PROJECT_SOURCE_DIR}/src/MaaCore/Utils/BattleGeometry.cpp)
target_include_directories(MaaMcpResourceLoadingTest PRIVATE ${PROJECT_SOURCE_DIR}/src/MaaCore)
target_link_libraries(MaaMcpResourceLoadingTest PRIVATE HeaderOnlyLibraries MaaUtils ${OpenCV_LIBS})
if(WIN32)
    target_sources(MaaMcpResourceLoadingTest PRIVATE
        ${PROJECT_SOURCE_DIR}/src/MaaCore/Utils/Platform/PlatformWin32.cpp)
    target_compile_definitions(MaaMcpResourceLoadingTest PRIVATE _UNICODE UNICODE)
endif()
add_test(NAME MaaMcpResourceLoadingTest COMMAND MaaMcpResourceLoadingTest)

add_executable(MaaMcpScrollImageStitchTest
    test_scroll_image_stitch.cpp
    ${PROJECT_SOURCE_DIR}/src/MaaCore/Utils/ScrollImageStitch.cpp
    ${PROJECT_SOURCE_DIR}/src/MaaCore/Vision/Matcher.cpp
    ${PROJECT_SOURCE_DIR}/src/MaaCore/Vision/MultiMatcher.cpp
    ${PROJECT_SOURCE_DIR}/src/MaaCore/Vision/MaskedCcoeffMatcher.cpp
    ${PROJECT_SOURCE_DIR}/src/MaaCore/Vision/Config/MatcherConfig.cpp
    ${PROJECT_SOURCE_DIR}/src/MaaCore/Vision/VisionHelper.cpp
    ${PROJECT_SOURCE_DIR}/src/MaaCore/InstHelper.cpp
    ${PROJECT_SOURCE_DIR}/src/MaaCore/Config/TemplResource.cpp
    ${PROJECT_SOURCE_DIR}/src/MaaCore/Config/GeneralConfig.cpp
    ${PROJECT_SOURCE_DIR}/src/MaaCore/Config/AbstractConfig.cpp)
target_include_directories(MaaMcpScrollImageStitchTest PRIVATE ${PROJECT_SOURCE_DIR}/src/MaaCore)
target_link_libraries(MaaMcpScrollImageStitchTest PRIVATE HeaderOnlyLibraries MaaUtils ${OpenCV_LIBS})
if(WIN32)
    target_sources(MaaMcpScrollImageStitchTest PRIVATE
        ${PROJECT_SOURCE_DIR}/src/MaaCore/Utils/Platform/PlatformWin32.cpp)
    target_compile_definitions(MaaMcpScrollImageStitchTest PRIVATE _UNICODE UNICODE)
endif()
add_test(NAME MaaMcpScrollImageStitchTest COMMAND MaaMcpScrollImageStitchTest)

add_custom_target(MaaMcpNativeTests DEPENDS
    MaaMcpPauseEvidenceTest MaaMcpBattleStateTest MaaMcpMediaTest MaaMcpResourceLoadingTest MaaMcpScrollImageStitchTest)

if(CMAKE_SYSTEM_NAME STREQUAL "Linux")
    add_executable(MaaMcpBattleObservationTest test_battle_observation.cpp)
    target_include_directories(MaaMcpBattleObservationTest PRIVATE ${PROJECT_SOURCE_DIR}/src/MaaCore)
    target_link_libraries(MaaMcpBattleObservationTest PRIVATE MaaCore HeaderOnlyLibraries MaaUtils ${OpenCV_LIBS})
    add_test(NAME MaaMcpBattleObservationTest COMMAND MaaMcpBattleObservationTest)
    add_dependencies(MaaMcpNativeTests MaaMcpBattleObservationTest)

    add_executable(MaaMcpAdbScreencapTransportTest test_adb_screencap_transport.cpp)
    target_include_directories(MaaMcpAdbScreencapTransportTest PRIVATE ${PROJECT_SOURCE_DIR}/src/MaaCore)
    target_link_libraries(MaaMcpAdbScreencapTransportTest PRIVATE MaaCore)
    add_test(NAME MaaMcpAdbScreencapTransportTest COMMAND MaaMcpAdbScreencapTransportTest)
    add_dependencies(MaaMcpNativeTests MaaMcpAdbScreencapTransportTest)
endif()
