Scriptname LumisMCM extends SKI_ConfigBase

; Reference to the Ghost Manager (kept for compatibility)
LumisGhostManager Property Manager Auto

; ------------------------------------------------------------------
; LOCAL VARIABLES
; ------------------------------------------------------------------
int Cache_Global = 0
int Cache_Local = 0
string Cache_UID = "Waiting..."
float LastHeartbeat = 0.0

Event OnConfigInit()
    ModName = "LUMIS"
    Pages = new string[1]
    Pages[0] = "Status"
    
    ; Register for the update event to track connection health
    RegisterForModEvent("Lumis_Update_End", "OnHeartbeat")
EndEvent

; ------------------------------------------------------------------
; VERSION CONTROL
; ------------------------------------------------------------------
int Function GetVersion()

EndFunction

Event OnVersionUpdate(int version)
    OnConfigInit() ; Re-register events
EndEvent

; ------------------------------------------------------------------
; HEARTBEAT LISTENER
; ------------------------------------------------------------------
; Runs whenever the C++ plugin sends data (approx every 1 second)
Event OnHeartbeat(string eventName, string strArg, float numArg, Form sender)
    ; The plugin sends "S:0:0" when offline, which we must ignore for status purposes.

    ; Parse the stats string directly (Format: "S:Global:Local:UID|...")
    if strArg != ""
        string[] parts = StringUtil.Split(strArg, "|")
        if parts.Length > 0
            ; Check for Stats Header
            if StringUtil.Find(parts[0], "S:") == 0
                string[] stats = StringUtil.Split(parts[0], ":")
                
                ; Only update Heartbeat if we have a full packet (including UID)
                ; This filters out the "S:0:0" kill signal.
                if stats.Length >= 4
                    LastHeartbeat = Utility.GetCurrentRealTime()
                endif

                ; 1. Parse Counts
                if stats.Length >= 3
                    Cache_Global = stats[1] as int
                    Cache_Local = stats[2] as int
                endif
                 
                ; 2. Parse UID
                if stats.Length >= 4
                    Cache_UID = stats[3]
                endif
            endif
        endif
    endif
EndEvent

; ------------------------------------------------------------------
; UI RENDERING
; ------------------------------------------------------------------
Event OnPageReset(string page)
    SetCursorFillMode(TOP_TO_BOTTOM)
    
    if page == "" || page == "Status"
        AddHeaderOption("LUMIS Multiverse Status")
        
        ; CHECK CONNECTION HEALTH
        float currentTime = Utility.GetCurrentRealTime()
        float timeDiff = currentTime - LastHeartbeat
        
        ; Connection is considered live if data was received in the last 4 seconds
        bool isConnected = (timeDiff >= 0.0) && (timeDiff < 4.0)
        
        if isConnected
            AddTextOption("Status:", "LIVE")
            AddTextOption("Total Connected Players:", Cache_Global as string)
            AddTextOption("Local Area Players:", Cache_Local as string)
        else
            AddTextOption("Status:", "DISCONNECTED")
            AddTextOption("Total Connected Players:", "0")
            AddTextOption("Local Area Players:", "0")
            
            ; Reset cache to prevent stale data
            Cache_Global = 0
            Cache_Local = 0
        endif
        
        AddEmptyOption()
        AddHeaderOption("Player Info")
        
        if isConnected
            AddTextOption("Your UID:", Cache_UID)
        else
            AddTextOption("Your UID:", "---")
        endif
        
        AddEmptyOption()
        AddHeaderOption("Controls")
        AddTextOption("Click here to refresh", "")
    endif
EndEvent

Event OnOptionSelect(int option)
    ; Force the page to redraw to show the latest numbers
    ForcePageReset()
EndEvent