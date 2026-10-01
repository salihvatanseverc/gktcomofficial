#pragma once

#include <string>

class GPMCore
{
public:
    void run();

private:
    void handleCommand(const std::string& command);
};