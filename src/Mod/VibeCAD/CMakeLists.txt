# SPDX-License-Identifier: LGPL-2.1-or-later

set(VibeCAD_Scripts
    Init.py
    InitGui.py
    VibeCADAuth.py
    VibeCADCore.py
    VibeCADDebug.py
    VibeCADGui.py
    VibeCADPreferences.py
    VibeCADProvider.py
    VibeCADProject.py
    VibeCADSession.py
    VibeCADTools.py
    VibeCADTransactions.py
    VibeCADWorkbenchTools.py
    requirements.txt
)

set(VibeCAD_Resources
    preferences-vibecad.svg
    vibecad-open-assistant.svg
    vibecad-send.svg
    vibecad-stop.svg
    vibecad-activity.svg
)

file(GLOB_RECURSE VibeCAD_ToolImplScripts
    CONFIGURE_DEPENDS
    RELATIVE ${CMAKE_CURRENT_SOURCE_DIR}
    tool_impl/*.py
)

add_custom_target(VibeCADScripts ALL
    SOURCES ${VibeCAD_Scripts} ${VibeCAD_ToolImplScripts} ${VibeCAD_Resources}
)

fc_target_copy_resource(VibeCADScripts
    ${CMAKE_CURRENT_SOURCE_DIR}
    ${CMAKE_BINARY_DIR}/Mod/VibeCAD
    ${VibeCAD_Scripts}
    ${VibeCAD_ToolImplScripts}
    ${VibeCAD_Resources}
)

install(
    FILES
        ${VibeCAD_Scripts}
        ${VibeCAD_Resources}
    DESTINATION
        Mod/VibeCAD
)

install(
    DIRECTORY
        tool_impl
    DESTINATION
        Mod/VibeCAD
    FILES_MATCHING
        PATTERN "*.py"
)
