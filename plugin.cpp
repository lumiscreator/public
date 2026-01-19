// 1. Windows and Standard Headers
#define WIN32_LEAN_AND_MEAN
#include <Windows.h>
#include <iostream>
#include <thread>
#include <chrono>
#include <string>
#include <vector>
#include <random>
#include <mutex>
#include <sstream>
#include <fstream>

// 2. SKSE and Skyrim Headers
#include <SKSE/SKSE.h>
#include <RE/Skyrim.h>

// 3. Networking/JSON
#define CPPHTTPLIB_OPENSSL_SUPPORT
#include "httplib.h"
#include "nlohmann/json.hpp"

using json = nlohmann::json;

#ifndef SKSE_EXPORT
    #define SKSE_EXPORT __declspec(dllexport)
#endif
#ifndef SKSEAPI
    #define SKSEAPI __stdcall
#endif

// Global states
static std::string g_playerUID = "";
static std::string g_apiToken = ""; 
static bool g_isConnected = false;

// Client Version Constant
const int CLIENT_VERSION = 101;

// File path for saving credentials
const std::string AUTH_FILE_PATH = "Data/SKSE/Plugins/LumisAuth.json";

extern "C" [[maybe_unused]] SKSE_EXPORT constinit SKSE::PluginVersionData SKSEPlugin_Version = []() {
    SKSE::PluginVersionData v;
    v.PluginVersion({ 1, 0, 0 });
    v.PluginName("LumisPlugin");
    v.AuthorName("Lumis");
    v.UsesAddressLibrary();
    v.HasNoStructUse(); 
    return v;
}();

void PrintToConsole(const char* a_message) {
    auto console = RE::ConsoleLog::GetSingleton();
    if (console) {
        console->Print(a_message);
    }
}

void SendPapyrusEvent(const char* eventName, const char* strArg, float numArg) {
    auto source = SKSE::GetModCallbackEventSource();
    if (source) {
        SKSE::ModCallbackEvent ev;
        ev.eventName = RE::BSFixedString(eventName);
        ev.strArg = RE::BSFixedString(strArg);
        ev.numArg = numArg;
        ev.sender = nullptr;
        source->SendEvent(&ev);
    }
}

// Returns true if Auth token is ready. Returns false if we need to retry.
bool LoadOrRegisterAuth(httplib::Client& cli, bool forceNew = false) {
    if (!forceNew) {
        std::ifstream inFile(AUTH_FILE_PATH);
        if (inFile.is_open()) {
            try {
                json j;
                inFile >> j;
                g_playerUID = j["uid"];
                g_apiToken = j["token"];
                SKSE::log::info("LUMIS: Loaded existing auth token for {}", g_playerUID);
                return true;
            } catch (...) {
                SKSE::log::warn("LUMIS: Auth file corrupted, re-registering...");
            }
        }
    }

    auto player = RE::PlayerCharacter::GetSingleton();
    int retries = 0;
    while ((!player || !player->GetDisplayFullName()) && retries < 60) {
        std::this_thread::sleep_for(std::chrono::milliseconds(1000));
        player = RE::PlayerCharacter::GetSingleton();
        retries++;
    }

    std::mt19937 rng(std::random_device{}());
    std::string playerName = (player && player->GetDisplayFullName()) ? player->GetDisplayFullName() : "Dragonborn";
    g_playerUID = playerName + "_" + std::to_string(rng());

    json reg_payload = { {"uid", g_playerUID} };
    auto res = cli.Post("/register", reg_payload.dump(), "application/json");

    if (res && res->status == 200) {
        try {
            json response = json::parse(res->body);
            g_apiToken = response["api_token"];
            
            std::ofstream outFile(AUTH_FILE_PATH);
            json save_data = { {"uid", g_playerUID}, {"token", g_apiToken} };
            outFile << save_data.dump(4);
            
            SKSE::log::info("LUMIS: Successfully registered. Token saved.");
            SKSE::GetTaskInterface()->AddTask([]() { PrintToConsole("LUMIS: Connected to Multiverse (New UID Registered)"); });
            return true;
        } catch (...) {
            SKSE::log::error("LUMIS: Failed to parse registration response.");
        }
    }
    return false;
}

void MultiverseBridge() {
    httplib::Client cli("https://lumisskyrim.pythonanywhere.com");
    cli.set_connection_timeout(0, 500000); 

    // Track the last time we had a successful connection
    auto lastSuccessTime = std::chrono::steady_clock::now();

    // Track the last time we warned the user about version
    auto lastVersionWarningTime = std::chrono::steady_clock::now() - std::chrono::seconds(70); 

    while (true) {
        // TANK MODE: Persistent Auth Retry
        if (g_apiToken.empty()) {
             if (!LoadOrRegisterAuth(cli, false)) {
                 std::this_thread::sleep_for(std::chrono::milliseconds(1000));
                 continue; 
             }
        }

        static float pX = 0, pY = 0, pZ = 0;
        static int pLoc = 0;
        static bool dataReady = false;

        SKSE::GetTaskInterface()->AddTask([&]() {
            auto player = RE::PlayerCharacter::GetSingleton();
            if (player && player->GetParentCell()) {
                 pX = player->GetPositionX();
                 pY = player->GetPositionY();
                 pZ = player->GetPositionZ();
                 
                 auto worldspace = player->GetWorldspace();
                 pLoc = worldspace ? (int)worldspace->GetFormID() : (int)player->GetParentCell()->GetFormID();
                 dataReady = true;
            }
        });

        if (!dataReady) {
            std::this_thread::sleep_for(std::chrono::milliseconds(1000));
            continue;
        }

        json local_data = {
            {"x", pX},
            {"y", pY},
            {"z", pZ},
            {"location", pLoc},
            {"version", CLIENT_VERSION}
        };

        httplib::Headers headers = {
            { "X-Lumis-Token", g_apiToken } 
        };

        auto res = cli.Post("/update", headers, local_data.dump(), "application/json");

        if (res && res->status == 200) {
            lastSuccessTime = std::chrono::steady_clock::now();

            try {
                json root = json::parse(res->body);

                // --- QUEUE LOGIC START ---
                if (root.contains("status") && root["status"] == "queued") {
                    g_isConnected = false;
                    int position = root["position"];
                    
                    SKSE::GetTaskInterface()->AddTask([position]() {
                        std::string msg = "LUMIS: Gateway at Capacity. Souls Ahead: " + std::to_string(position);
                        PrintToConsole(msg.c_str());
                    });
                    
                    SKSE::GetTaskInterface()->AddTask([]() {
                        SendPapyrusEvent("Lumis_Update_End", "S:0:0|", 0.0);
                    });

                    std::this_thread::sleep_for(std::chrono::seconds(60));
                    continue; 
                }

                // --- ACTIVE LOGIC ---
                if (!g_isConnected) {
                    SKSE::GetTaskInterface()->AddTask([]() { PrintToConsole("LUMIS: Connected to Multiverse"); });
                    g_isConnected = true;
                }
				
                int globalCount = 0;
                int localCount = 0;
                json server_list;

                if (root.contains("meta")) {
                    globalCount = root["meta"]["global"];
                    localCount = root["meta"]["local"];
                    server_list = root["ghosts"];
                } else {
                    server_list = root; 
                }
                
                SKSE::GetTaskInterface()->AddTask([server_list, globalCount, localCount]() {
                    std::stringstream ss;
                    
                    ss << "S:" << globalCount << ":" << localCount << ":" << g_playerUID << "|";

                    int count = 0;
                    for (auto& ghost : server_list) {
                        count++;
                        if (count > 8) break; 
                        if (count > 1) ss << "|";
                        ss << count << ":" 
                           << (float)ghost["x"] << ":" 
                           << (float)ghost["y"] << ":" 
                           << (float)ghost["z"] << ":" 
                           << (int)ghost["location"];
                    }
                    SendPapyrusEvent("Lumis_Update_End", ss.str().c_str(), (float)count);
                });

            } catch (...) {}
            
            std::this_thread::sleep_for(std::chrono::milliseconds(1000));

        } else if (res && res->status == 426) {
             // Version Mismatch Logic
             auto now = std::chrono::steady_clock::now();
             auto elapsedWarning = std::chrono::duration_cast<std::chrono::seconds>(now - lastVersionWarningTime).count();
             
             if (elapsedWarning >= 60) {
                 SKSE::GetTaskInterface()->AddTask([]() { 
                     PrintToConsole("Your LUMIS mod is outdated. Please download the latest version from Nexus"); 
                 });
                 lastVersionWarningTime = now;
             }

             SKSE::GetTaskInterface()->AddTask([]() {
                 SendPapyrusEvent("Lumis_Update_End", "S:0:0|", 0.0);
             });
             
             std::this_thread::sleep_for(std::chrono::milliseconds(1000));

        } else if (res && res->status == 403) {
             // Token Expired Logic
             SKSE::GetTaskInterface()->AddTask([]() { PrintToConsole("LUMIS: Token Expired. Attempting Recovery..."); });
             if (LoadOrRegisterAuth(cli, true)) {
                 lastSuccessTime = std::chrono::steady_clock::now();
                 SKSE::GetTaskInterface()->AddTask([]() { PrintToConsole("LUMIS: Recovery Successful."); });
             } else {
                 if (g_isConnected) {
                    SKSE::GetTaskInterface()->AddTask([]() { PrintToConsole("LUMIS: Auth Failed."); });
                    g_isConnected = false;
                 }
             }
             std::this_thread::sleep_for(std::chrono::milliseconds(1000));

        } else {
            // --- OFFLINE / CONNECTION LOST ---
            if (g_isConnected) {
                SKSE::GetTaskInterface()->AddTask([]() { PrintToConsole("LUMIS: Multiverse unstable... Collapse in 30 seconds"); });
                g_isConnected = false;
            }

            auto now = std::chrono::steady_clock::now();
            auto elapsed = std::chrono::duration_cast<std::chrono::seconds>(now - lastSuccessTime).count();

            if (elapsed > 30) {
                SKSE::GetTaskInterface()->AddTask([]() {
                    SendPapyrusEvent("Lumis_Update_End", "S:0:0|", 0.0);
                });
            } 
            std::this_thread::sleep_for(std::chrono::milliseconds(1000));
        }
    }
}

void OnMessage(SKSE::MessagingInterface::Message* a_msg) {
    if (a_msg->type == SKSE::MessagingInterface::kDataLoaded) {
        std::thread(MultiverseBridge).detach();
        SKSE::log::info("LUMIS: Bridge is live!");
    }
}

extern "C" [[maybe_unused]] SKSE_EXPORT bool SKSEAPI SKSEPlugin_Load(const SKSE::LoadInterface* a_skse) {
    SKSE::Init(a_skse);
    auto messaging = SKSE::GetMessagingInterface();
    if (messaging) {
        messaging->RegisterListener(OnMessage);
    }
    return true;
}