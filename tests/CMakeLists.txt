cmake_minimum_required(VERSION 3.22)
project(titanium_ru_policy_tests LANGUAGES C CXX ASM)
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(BUILD_TESTING OFF CACHE BOOL "" FORCE)
if(NOT IS_DIRECTORY "${BORINGSSL_SOURCE_DIR}/pki")
  message(FATAL_ERROR "Set BORINGSSL_SOURCE_DIR to the pinned BoringSSL checkout")
endif()
add_subdirectory("${BORINGSSL_SOURCE_DIR}" boringssl EXCLUDE_FROM_ALL)
file(MAKE_DIRECTORY "${CMAKE_CURRENT_BINARY_DIR}/include/third_party/boringssl")
file(CREATE_LINK "${BORINGSSL_SOURCE_DIR}"
     "${CMAKE_CURRENT_BINARY_DIR}/include/third_party/boringssl/src" SYMBOLIC)
add_executable(scoped_ca_test scoped_ca_test.cc)
target_include_directories(scoped_ca_test PRIVATE
  "${CMAKE_CURRENT_SOURCE_DIR}/../chromium_overlay"
  "${CMAKE_CURRENT_BINARY_DIR}/include")
target_link_libraries(scoped_ca_test PRIVATE pki crypto)
enable_testing()
add_test(NAME scoped_ca_policy COMMAND scoped_ca_test)
