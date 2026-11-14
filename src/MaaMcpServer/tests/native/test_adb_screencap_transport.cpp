#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iostream>

#include "Controller/AdbController.h"
#include "MaaUtils/NoWarningCV.hpp"
#include "Utils/Platform.hpp"

void expect_transport(bool condition, const char* name)
{
    if (!condition) {
        std::cerr << "Failed: " << name << '\n';
        std::exit(1);
    }
}

class ScreencapTestIO final : public asst::PlatformIO
{
public:
    struct Call
    {
        std::string command;
        bool by_socket;
        int64_t timeout;
    };

    ScreencapTestIO()
    {
        m_support_socket = true;
        std::vector<unsigned char> encoded;
        expect_transport(
            cv::imencode(".png", cv::Mat(2, 3, CV_8UC3, cv::Scalar(10, 20, 30)), encoded),
            "encode fixture PNG");
        png.assign(encoded.begin(), encoded.end());
    }

    std::optional<int> call_command(
        const std::string& command,
        bool by_socket,
        std::string& pipe_data,
        std::string&,
        int64_t timeout,
        std::chrono::steady_clock::time_point) override
    {
        calls.push_back({ command, by_socket, timeout });
        if (command == "devices") {
            pipe_data = "fixture\tdevice";
        }
        else if (command == "uuid") {
            pipe_data = "0123456789abcdef";
        }
        else if (command == "version") {
            pipe_data = "12";
        }
        else if (command == "display") {
            pipe_data = "3 2";
        }
        else if (command == "nc-address") {
            pipe_data = "10.0.2.2 route";
        }
        else if (command == "png" && !fail_png) {
            pipe_data = png;
        }
        else {
            return 1;
        }
        return 0;
    }

    std::optional<unsigned short> init_socket(const std::string&) override
    {
        ++socket_starts;
        return 4321;
    }

    void close_socket() noexcept override {}

    std::shared_ptr<asst::IOHandler> interactive_shell(const std::string&) override { return nullptr; }

    void release_adb(const std::string&, int64_t) override {}

    std::vector<Call> calls;
    std::string png;
    int socket_starts = 0;
    bool fail_png = false;
};

class ScreencapTestController final : public asst::AdbController
{
public:
    ScreencapTestController(const std::shared_ptr<ScreencapTestIO>& io, const asst::AsstCallback& callback = {}) :
        AdbController(callback, nullptr, asst::PlatformType::Native)
    {
        m_platform_io = io;
        m_support_socket = io->m_support_socket;
    }

    bool reject_empty(bool by_socket)
    {
        m_server_started = true;
        bool decoded = false;
        const auto result = screencap(
            "",
            [&](const std::string&) {
                decoded = true;
                return true;
            },
            true,
            by_socket);
        return result == ScreencapResult::Failed && !decoded;
    }

    using AdbController::screencap;

    int reconnects = 0;

protected:
    std::optional<std::string> reconnect(const std::string& command, int64_t, bool by_socket) override
    {
        expect_transport(command == "png" && !by_socket, "reconnect retains configured PNG command");
        ++reconnects;
        return std::static_pointer_cast<ScreencapTestIO>(m_platform_io)->png;
    }
};

void load_transport_config()
{
    const json::value config = json::object {
        { "version", "test" },
        { "options",
          json::object {
              { "taskDelay", 0 },
              { "SSSFightScreencapInterval", 0 },
              { "RoguelikeFightScreencapInterval", 0 },
              { "CopilotFightScreencapInterval", 0 },
              { "controlDelayRange", json::array { 0, 0 } },
          } },
        { "packageName", json::object {} },
        { "connection",
          json::array {
              json::object {
                  { "configName", "PngOnly" },
                  { "devices", "devices" },
                  { "addressRegex", "(.+)\tdevice" },
                  { "uuid", "uuid" },
                  { "version", "version" },
                  { "display", "display" },
                  { "ncAddress", "nc-address" },
                  { "screencapEncode", "png" },
              },
              json::object {
                  { "configName", "NoCapture" },
                  { "baseConfig", "PngOnly" },
                  { "screencapEncode", "" },
              },
              json::object {
                  { "configName", "ConfiguredFallback" },
                  { "baseConfig", "PngOnly" },
                  { "screencapRawByNC", "nc [NcAddress] [NcPort]" },
                  { "screencapRawWithGzip", "gzip" },
              },
          } },
    };
    const auto path = asst::utils::path("adb-screencap-transport-config.json");
    {
        std::ofstream output(path);
        output << config.to_string();
    }
    const bool loaded = asst::Config.load(path);
    std::filesystem::remove(path);
    expect_transport(loaded, "load local transport configuration");
}

int main()
{
#ifndef ASST_DEBUG
    std::cerr << "This regression must run in a Debug build\n";
    return 1;
#endif
    load_transport_config();
    {
        const auto io = std::make_shared<ScreencapTestIO>();
        ScreencapTestController controller(io);
        expect_transport(controller.reject_empty(false), "reject empty pipe capture");
        expect_transport(controller.reject_empty(true), "reject empty socket capture");
        expect_transport(io->calls.empty(), "empty captures never reach platform command execution");
        expect_transport(controller.reconnects == 0, "empty captures never reconnect");
    }
    for (const auto* config : { "PngOnly", "NoCapture" }) {
        const auto io = std::make_shared<ScreencapTestIO>();
        json::value diagnostics;
        ScreencapTestController controller(io, [&](asst::AsstMsg, const json::value& info, asst::Assistant*) {
            if (info.get("what", "") == "FastestWayToScreencap") {
                diagnostics = info.at("details");
            }
        });
        expect_transport(controller.connect("unused", "fixture", config), "connect local fixture");
        expect_transport(io->socket_starts == 0, "unconfigured NC never starts a listener");
        expect_transport(io->calls.size() == 4, "unconfigured NC never queries its address");
        io->calls.clear();
        cv::Mat image;
        if (std::string_view(config) == "NoCapture") {
            expect_transport(!controller.screencap(image), "no configured captures fail without probing");
            expect_transport(image.empty() && io->calls.empty(), "no configured capture executes a command");
            continue;
        }
        expect_transport(controller.screencap(image), "configured PNG probe succeeds");
        expect_transport(image.rows == 2 && image.cols == 3, "configured PNG decodes");
        expect_transport(image.at<cv::Vec3b>(0, 0) == cv::Vec3b(10, 20, 30), "configured PNG pixels preserved");
        expect_transport(io->calls.size() == 1 && io->calls.front().command == "png", "only PNG is probed");
        expect_transport(diagnostics.at("method").as_string() == "Encode", "selected transport diagnostic");
        const auto& alternatives = diagnostics.at("alternatives").as_array();
        expect_transport(
            alternatives.size() == 3 && alternatives[0].at("cost").as_string() == "???" &&
                alternatives[1].at("cost").as_string() == "???",
            "unsupported transport diagnostics retained");
        expect_transport(controller.screencap(image), "selected PNG transport remains usable");
        io->fail_png = true;
        expect_transport(controller.screencap(image, true), "configured PNG retains reconnect fallback");
        expect_transport(controller.reconnects == 1, "configured PNG failure reaches reconnect");
    }
    {
        const auto io = std::make_shared<ScreencapTestIO>();
        ScreencapTestController controller(io);
        expect_transport(controller.connect("unused", "fixture", "ConfiguredFallback"), "connect configured NC");
        expect_transport(io->socket_starts == 1 && io->calls.back().command == "nc-address", "configured NC starts");
        io->calls.clear();
        cv::Mat image;
        expect_transport(controller.screencap(image), "failed configured transports fall back to PNG");
        expect_transport(
            io->calls.size() == 3 && io->calls[0].command == "nc 10.0.2.2 4321" && io->calls[1].command == "gzip" &&
                io->calls[2].command == "png",
            "configured transports retain probe order");
        expect_transport(
            io->calls[0].by_socket && io->calls[0].timeout == 5000 && !io->calls[1].by_socket &&
                io->calls[1].timeout == 20'000 && !io->calls[2].by_socket && io->calls[2].timeout == 20'000,
            "configured transports retain socket mode and timeouts");
    }
    std::cout << "Native screenshot transport regressions passed\n";
}
