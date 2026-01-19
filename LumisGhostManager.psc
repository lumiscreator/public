Scriptname LumisGhostManager extends Quest

Form Property LUMIS_Avatar Auto  
ObjectReference[] GhostPool          

; Stats for MCM (From v20)
int Property Stat_GlobalPlayers Auto Hidden
int Property Stat_LocalPlayers Auto Hidden

; Smoothing Arrays
float[] TargetX
float[] TargetY
float[] TargetZ
int[]   TargetLoc
float[] lastX
float[] lastY
float[] lastZ

; State Tracking
int LastPlayerLoc = 0

Event OnInit()
    GhostPool = new ObjectReference[9]
    TargetX = new float[9]
    TargetY = new float[9]
    TargetZ = new float[9]
    TargetLoc = new int[9]
    
    lastX = new float[9]
    lastY = new float[9]
    lastZ = new float[9]
    
    LastPlayerLoc = 0

    RegisterForModEvent("Lumis_Update_End", "OnUpdateEnd")
    
    Debug.Notification("LUMIS: Ghost Manager Initialized")
EndEvent

; ------------------------------------------------------------------
; [FIXED] RESTORED MISSING EVENT FROM 12-W
; Handles loading saves, cleaning up orphans, and re-hooking events
; ------------------------------------------------------------------
Event OnPlayerLoadGame()
    ; Safety Cleanup
    int j = 0
    while (j < 9)
        if GhostPool[j]
            GhostPool[j].Disable()
            GhostPool[j].Delete()
            GhostPool[j] = None
        endif
        j += 1
    endWhile
    
    LastPlayerLoc = 0
    
    ; Re-register for the C++ event (Essential for persistence)
    RegisterForModEvent("Lumis_Update_End", "OnUpdateEnd")
EndEvent

; ------------------------------------------------------------------
; MAIN LOOP
; ------------------------------------------------------------------
Event OnUpdateEnd(string eventName, string strArg, float numArg, Form sender)
    int ghostCount = numArg as int
    ObjectReference playerRef = Game.GetPlayer()
    
    ; 1. PARSE DATA
    if strArg != ""
        string[] allData = StringUtil.Split(strArg, "|")
        int k = 0
        
        ; Check for Stats Header (S:Global:Local)
        if StringUtil.Find(allData[0], "S:") == 0
            string[] stats = StringUtil.Split(allData[0], ":")
            Stat_GlobalPlayers = stats[1] as int
            Stat_LocalPlayers = stats[2] as int
            k = 1 
        else
            Stat_GlobalPlayers = 0
            Stat_LocalPlayers = 0
            k = 0
        endif

        while (k < allData.Length)
            string[] parts = StringUtil.Split(allData[k], ":")
            if parts.Length >= 5
                int index = parts[0] as int
                if index > 0 && index <= 8
                    TargetX[index] = parts[1] as float
                    TargetY[index] = parts[2] as float
                    TargetZ[index] = parts[3] as float
                    TargetLoc[index] = parts[4] as int
                endif
            endif
            k += 1
        endWhile
    endif

    ; 2. RENDER LOGIC
    if !LUMIS_Avatar
        LUMIS_Avatar = Game.GetFormFromFile(0x000083BA, "LumisCore.esp")
    endif

    float pX = playerRef.GetPositionX()
    float pY = playerRef.GetPositionY()
    float pZ = playerRef.GetPositionZ()
    
    int pLoc = 0
    WorldSpace pWorld = playerRef.GetWorldSpace()
    if pWorld
        pLoc = pWorld.GetFormID()
    else
        pLoc = playerRef.GetParentCell().GetFormID()
    endif

    bool playerChangedCell = false
    if pLoc != LastPlayerLoc
        playerChangedCell = true
        LastPlayerLoc = pLoc
    endif

    int i = 1
    while (i <= 8)
        ; If we are offline, plugin sends ghostCount = 0
        bool slotActive = (i <= ghostCount)
        
        if slotActive
            if (pLoc != TargetLoc[i])
                slotActive = false
            endif

            if slotActive
                float dx = TargetX[i] - pX
                float dy = TargetY[i] - pY
                float dz = TargetZ[i] - pZ
                float distSq = (dx*dx) + (dy*dy) + (dz*dz)
                
                ; 4500 units cutoff (Squared)
                if (distSq > 20250000.0)
                    slotActive = false
                endif
            endif
        endif

        if slotActive
            if !GhostPool[i]
                GhostPool[i] = playerRef.PlaceAtMe(LUMIS_Avatar, 1, false, true)
            endif

            if GhostPool[i].IsDisabled() || playerChangedCell
                GhostPool[i].MoveTo(playerRef) 
                GhostPool[i].SetPosition(TargetX[i], TargetY[i], TargetZ[i] + 50.0)
                GhostPool[i].Enable()
                
                lastX[i] = TargetX[i]
                lastY[i] = TargetY[i]
                lastZ[i] = TargetZ[i]
            else
                float moveDx = TargetX[i] - lastX[i]
                float moveDy = TargetY[i] - lastY[i]
                float moveDz = TargetZ[i] - lastZ[i]
                float moveDist = Math.sqrt(moveDx*moveDx + moveDy*moveDy + moveDz*moveDz)

                if (moveDist > 2000.0)
                    GhostPool[i].SetPosition(TargetX[i], TargetY[i], TargetZ[i] + 50.0)
                else
                    float moveSpeed = moveDist
                    if moveSpeed < 50.0
                        moveSpeed = 50.0
                    endif
                    GhostPool[i].TranslateTo(TargetX[i], TargetY[i], TargetZ[i] + 50.0, 0.0, 0.0, 0.0, moveSpeed + 20.0)
                endif

                lastX[i] = TargetX[i]
                lastY[i] = TargetY[i]
                lastZ[i] = TargetZ[i]
            endif

        else 
            ; Cleanup
            if GhostPool[i]
                if !GhostPool[i].IsDisabled()
                    GhostPool[i].Disable()
                endif
            endif
        endif
        
        i += 1
    endWhile
EndEvent