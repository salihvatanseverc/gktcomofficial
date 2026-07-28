cmake_minimum_required(VERSION 3.16)

project(GokturkStudio)

set(CMAKE_CXX_STANDARD 17)

set(CMAKE_AUTOMOC ON)
set(CMAKE_AUTOUIC ON)
set(CMAKE_AUTORCC ON)

find_package(Qt6 REQUIRED COMPONENTS Widgets)

add_executable(GokturkStudio
    main.cpp
    MainWindow.cpp
    MainWindow.h
)

target_link_libraries(GokturkStudio
    Qt6::Widgets
)