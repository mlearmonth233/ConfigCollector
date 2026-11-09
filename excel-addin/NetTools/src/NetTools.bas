Attribute VB_Name = "NetTools"
'==============================================================================
' NetTools - Excel ribbon add-in for network engineers
'
' Check DNS : forward lookup, reverse (PTR) lookup, forward/reverse match
'             and ping for a column of IP addresses or hostnames.
' DNS Only  : the lookups without the ping.
' Ping Only : status, latency and TTL.
'
' DNS uses the Windows resolver (ws2_32 getaddrinfo / getnameinfo), so it
' answers exactly as the PC would (hosts file, DNS suffixes, etc.).
' Ping uses WMI Win32_PingStatus - no console windows flash up.
'
' Worksheet functions are included too:
'   =NT_RESOLVE(A2)   -> IP address(es) for a hostname
'   =NT_PTR(A2)       -> reverse DNS name for an IP (or a hostname's IP)
'   =NT_PING(A2)      -> "Up (12 ms)" / "Timed out" / ...
'
' Requires Excel 2010 or later (VBA7), 32- or 64-bit, on Windows.
'==============================================================================
Option Explicit

' ---- Settings ----------------------------------------------------------------
Private Const PING_TIMEOUT_MS As Long = 1000     ' per host
Private Const APP_NAME As String = "NetTools"
Private Const APP_VERSION As String = "1.0.0"

' ---- Result column headers ---------------------------------------------------
Private Const H_TYPE As String = "Input Type"
Private Const H_IPS As String = "Resolved IP(s)"
Private Const H_PTR As String = "Reverse DNS (PTR)"
Private Const H_MATCH As String = "Fwd/Rev Match"
Private Const H_PINGIP As String = "Pinged Address"
Private Const H_PING As String = "Ping"
Private Const H_LAT As String = "Latency (ms)"
Private Const H_TTL As String = "TTL"

' ---- Winsock -----------------------------------------------------------------
Private Const AF_INET As Long = 2
Private Const AF_INET6 As Long = 23
Private Const SOCK_STREAM As Long = 1
Private Const AI_NUMERICHOST As Long = 4
Private Const NI_NUMERICHOST As Long = 2
Private Const NI_NAMEREQD As Long = 4
Private Const NI_MAXHOST As Long = 1025

#If Win64 Then
Private Const PTR_SIZE As Long = 8
#Else
Private Const PTR_SIZE As Long = 4
#End If

' struct addrinfo { int flags; int family; int socktype; int protocol;
'                   size_t addrlen; char *canonname; sockaddr *addr; addrinfo *next; }
Private Const AI_OFS_FAMILY As Long = 4
Private Const AI_OFS_ADDRLEN As Long = 16
Private Const AI_OFS_ADDR As Long = 16 + 2 * PTR_SIZE
Private Const AI_OFS_NEXT As Long = 16 + 3 * PTR_SIZE

Private Declare PtrSafe Function WSAStartup Lib "ws2_32.dll" ( _
    ByVal wVersionRequested As Integer, ByRef lpWSAData As Any) As Long
Private Declare PtrSafe Function WSACleanup Lib "ws2_32.dll" () As Long
Private Declare PtrSafe Function getaddrinfo Lib "ws2_32.dll" ( _
    ByVal pNodeName As String, ByVal pServiceName As LongPtr, _
    ByVal pHints As LongPtr, ByRef ppResult As LongPtr) As Long
Private Declare PtrSafe Sub freeaddrinfo Lib "ws2_32.dll" (ByVal pAddrInfo As LongPtr)
Private Declare PtrSafe Function getnameinfo Lib "ws2_32.dll" ( _
    ByVal pSockaddr As LongPtr, ByVal SockaddrLength As Long, _
    ByVal pNodeBuffer As String, ByVal NodeBufferSize As Long, _
    ByVal pServiceBuffer As LongPtr, ByVal ServiceBufferSize As Long, _
    ByVal Flags As Long) As Long
Private Declare PtrSafe Sub CopyMemory Lib "kernel32" Alias "RtlMoveMemory" ( _
    ByRef Destination As Any, ByVal Source As LongPtr, ByVal Length As LongPtr)

Private mWmi As Object

' Result of checking one host
Private Type HostResult
    InputType As String
    Addresses As String     ' comma separated
    FirstAddr As String     ' address that gets pinged
    Ptr As String
    Match As String
    PingStatus As String
    Latency As Variant
    Ttl As Variant
    DnsFailed As Boolean
    PingUp As Boolean
End Type

'==============================================================================
' Ribbon callbacks
'==============================================================================
Public Sub NT_OnCheckDns(control As IRibbonControl)
    RunChecks True, True
End Sub

Public Sub NT_OnDnsOnly(control As IRibbonControl)
    RunChecks True, False
End Sub

Public Sub NT_OnPingOnly(control As IRibbonControl)
    RunChecks False, True
End Sub

Public Sub NT_OnClearResults(control As IRibbonControl)
    ClearResults
End Sub

Public Sub NT_OnAbout(control As IRibbonControl)
    MsgBox APP_NAME & " " & APP_VERSION & vbCrLf & vbCrLf & _
        "1. Select a column of IP addresses or hostnames" & vbCrLf & _
        "   (select just the first cell to run to the end of the list)." & vbCrLf & _
        "2. Click Check DNS, DNS Only or Ping Only." & vbCrLf & _
        "3. Results appear in the columns to the right. Press Esc to stop." & vbCrLf & vbCrLf & _
        "A header row (e.g. 'Hostname' or 'IP Address') is detected and skipped." & vbCrLf & vbCrLf & _
        "Worksheet functions: =NT_RESOLVE(A2)  =NT_PTR(A2)  =NT_PING(A2)" & vbCrLf & vbCrLf & _
        "Ping timeout: " & PING_TIMEOUT_MS & " ms per host.", _
        vbInformation, "About " & APP_NAME
End Sub

' Same actions as plain macros (Alt+F8 / quick access toolbar)
Public Sub NetTools_CheckDns()
    RunChecks True, True
End Sub

Public Sub NetTools_DnsOnly()
    RunChecks True, False
End Sub

Public Sub NetTools_PingOnly()
    RunChecks False, True
End Sub

'==============================================================================
' Main routine
'==============================================================================
Private Sub RunChecks(ByVal doDns As Boolean, ByVal doPing As Boolean)
    Dim ws As Worksheet
    Dim col As Long, firstRow As Long, lastRow As Long, headerRow As Long
    Dim headers As Variant, nOut As Long, outHeaderRow As Long
    Dim r As Long, total As Long, done As Long
    Dim host As String, res As HostResult, rowVals() As Variant
    Dim nUp As Long, nDown As Long, nDnsFail As Long, nMismatch As Long
    Dim t0 As Double, wsaOk As Boolean, msg As String

    If ActiveSheet Is Nothing Then Exit Sub
    If TypeName(Selection) <> "Range" Then
        MsgBox "Select the cells that hold the IP addresses or hostnames first.", vbExclamation, APP_NAME
        Exit Sub
    End If
    Set ws = ActiveSheet
    If ws.ProtectContents Then
        MsgBox "This sheet is protected - unprotect it so NetTools can write results.", vbExclamation, APP_NAME
        Exit Sub
    End If

    If Not GetInputRows(ws, Selection, col, firstRow, lastRow, headerRow) Then Exit Sub

    headers = BuildHeaders(doDns, doPing)
    nOut = UBound(headers) - LBound(headers) + 1

    ' Where the result headers go: the detected header row, else the row
    ' above the data if that's empty, else nowhere.
    If headerRow > 0 Then
        outHeaderRow = headerRow
    ElseIf firstRow > 1 Then
        If Application.WorksheetFunction.CountA( _
            ws.Range(ws.Cells(firstRow - 1, col), ws.Cells(firstRow - 1, col + nOut))) = 0 Then
            outHeaderRow = firstRow - 1
        End If
    End If

    If Not PrepareOutputArea(ws, col, nOut, IIf(outHeaderRow > 0, outHeaderRow, firstRow), lastRow, outHeaderRow) Then Exit Sub

    total = Application.WorksheetFunction.CountA(ws.Range(ws.Cells(firstRow, col), ws.Cells(lastRow, col)))
    If total = 0 Then
        MsgBox "There are no hostnames or IP addresses in the selected cells.", vbExclamation, APP_NAME
        Exit Sub
    End If

    ' Header cells
    If outHeaderRow > 0 Then
        With ws.Range(ws.Cells(outHeaderRow, col + 1), ws.Cells(outHeaderRow, col + nOut))
            .Value = headers
            .Font.Bold = True
        End With
    End If
    ' Text columns stay text (stops Excel turning e.g. IPv6 or "1/2" into numbers/dates)
    FormatOutputColumns ws, col, firstRow, lastRow, headers

    t0 = Timer
    Application.EnableCancelKey = xlErrorHandler
    On Error GoTo Handler

    wsaOk = WsaInit()
    If doDns And Not wsaOk Then
        MsgBox "Could not start Windows networking (WSAStartup failed).", vbCritical, APP_NAME
        GoTo Cleanup
    End If

    For r = firstRow To lastRow
        host = CleanHost(ws.Cells(r, col).Value)
        If Len(host) > 0 Then
            done = done + 1
            Application.StatusBar = APP_NAME & ": " & host & "  (" & done & " of " & total & ")   -   press Esc to stop"

            res = CheckHost(host, doDns, doPing)
            rowVals = ResultToRow(res, doDns, doPing)
            ws.Range(ws.Cells(r, col + 1), ws.Cells(r, col + nOut)).Value = rowVals
            ColourRow ws, r, col, headers, res

            If res.DnsFailed Then nDnsFail = nDnsFail + 1
            If doDns And Left$(res.Match, 2) = "No" Then nMismatch = nMismatch + 1
            If doPing Then
                If res.PingUp Then nUp = nUp + 1 Else nDown = nDown + 1
            End If
            DoEvents
        End If
    Next r

    msg = "Checked " & done & " host(s) in " & Format$(ElapsedSeconds(t0), "0.0") & " s." & vbCrLf & vbCrLf
    If doDns Then
        msg = msg & "DNS lookup failed: " & nDnsFail & vbCrLf & _
                    "Forward/reverse mismatch or no PTR: " & nMismatch & vbCrLf
    End If
    If doPing Then
        msg = msg & "Ping up: " & nUp & vbCrLf & "Ping down: " & nDown & vbCrLf
    End If
    If outHeaderRow = 0 Then
        msg = msg & vbCrLf & "Columns: " & Join(headers, " | ")
    End If
    Application.StatusBar = False
    MsgBox msg, vbInformation, APP_NAME
    GoTo Cleanup

Handler:
    If Err.Number = 18 Then
        Application.StatusBar = False
        MsgBox "Stopped after " & done & " of " & total & " host(s).", vbInformation, APP_NAME
    Else
        Application.StatusBar = False
        MsgBox "Error " & Err.Number & ": " & Err.Description, vbCritical, APP_NAME
    End If

Cleanup:
    On Error Resume Next
    If wsaOk Then WSACleanup
    Application.StatusBar = False
    Application.EnableCancelKey = xlInterrupt
End Sub

' Works out which column/rows to check from the selection.
Private Function GetInputRows(ByVal ws As Worksheet, ByVal sel As Range, _
        ByRef col As Long, ByRef firstRow As Long, ByRef lastRow As Long, _
        ByRef headerRow As Long) As Boolean
    Dim rng As Range

    Set rng = sel.Areas(1).Columns(1)
    col = rng.Column

    If rng.Cells.CountLarge = 1 Then
        ' Single cell: run from here to the last value in the column
        firstRow = rng.Row
        lastRow = ws.Cells(ws.Rows.Count, col).End(xlUp).Row
        If lastRow < firstRow Then lastRow = firstRow
    Else
        ' Trim whole-column selections to the used range
        Set rng = Intersect(rng, ws.UsedRange)
        If rng Is Nothing Then
            MsgBox "The selected column is empty.", vbExclamation, APP_NAME
            Exit Function
        End If
        firstRow = rng.Row
        lastRow = rng.Row + rng.Rows.Count - 1
    End If

    If col + 8 > ws.Columns.Count Then
        MsgBox "There is no room to the right of this column for results.", vbExclamation, APP_NAME
        Exit Function
    End If

    If LooksLikeHeader(CStr(ws.Cells(firstRow, col).Text)) Then
        headerRow = firstRow
        firstRow = firstRow + 1
    End If
    If firstRow > lastRow Then
        MsgBox "There are no hostnames or IP addresses below the header.", vbExclamation, APP_NAME
        Exit Function
    End If
    GetInputRows = True
End Function

Private Function LooksLikeHeader(ByVal s As String) As Boolean
    s = LCase$(Trim$(s))
    If Len(s) = 0 Then Exit Function
    If InStr(s, " ") > 0 Then LooksLikeHeader = True: Exit Function
    Select Case s
        Case "host", "hosts", "hostname", "hostnames", "host_name", "ip", "ips", "ipaddress", _
             "ip_address", "ip-address", "address", "addresses", "name", "names", "device", _
             "devices", "fqdn", "server", "servers", "target", "targets", "node", "nodes", _
             "dns", "dnsname", "dns_name"
            LooksLikeHeader = True
    End Select
End Function

Private Function BuildHeaders(ByVal doDns As Boolean, ByVal doPing As Boolean) As Variant
    If doDns And doPing Then
        BuildHeaders = Array(H_TYPE, H_IPS, H_PTR, H_MATCH, H_PING, H_LAT, H_TTL)
    ElseIf doDns Then
        BuildHeaders = Array(H_TYPE, H_IPS, H_PTR, H_MATCH)
    Else
        BuildHeaders = Array(H_PINGIP, H_PING, H_LAT, H_TTL)
    End If
End Function

Private Function IsNetToolsHeader(ByVal s As String) As Boolean
    Select Case s
        Case H_TYPE, H_IPS, H_PTR, H_MATCH, H_PINGIP, H_PING, H_LAT, H_TTL
            IsNetToolsHeader = True
    End Select
End Function

' Makes sure results won't silently overwrite the user's data.
' Re-running over earlier NetTools results just overwrites them.
Private Function PrepareOutputArea(ByVal ws As Worksheet, ByVal col As Long, ByVal nOut As Long, _
        ByVal topRow As Long, ByVal lastRow As Long, ByVal outHeaderRow As Long) As Boolean
    Dim area As Range, c As Long, previousRun As Boolean, ans As VbMsgBoxResult, nOld As Long

    ' Earlier NetTools run? (header row holds our headers)
    If outHeaderRow > 0 Then
        c = col + 1
        Do While IsNetToolsHeader(CStr(ws.Cells(outHeaderRow, c).Value))
            nOld = nOld + 1
            c = c + 1
        Loop
        previousRun = (nOld > 0)
    End If

    If previousRun Then
        ' Clear the old result block (it may be wider/narrower than this run)
        With ws.Range(ws.Cells(outHeaderRow, col + 1), ws.Cells(lastRow, col + nOld))
            .ClearContents
            .ClearFormats
        End With
        If nOld >= nOut Then PrepareOutputArea = True: Exit Function
        ' Need more columns than last time - check the extra ones are free
        Set area = ws.Range(ws.Cells(topRow, col + nOld + 1), ws.Cells(lastRow, col + nOut))
    Else
        Set area = ws.Range(ws.Cells(topRow, col + 1), ws.Cells(lastRow, col + nOut))
    End If

    If Application.WorksheetFunction.CountA(area) = 0 Then
        PrepareOutputArea = True
        Exit Function
    End If

    ans = MsgBox("The " & nOut & " columns to the right of the selected column already contain data." & vbCrLf & vbCrLf & _
                 "Yes  = insert new columns for the results (nothing is lost)" & vbCrLf & _
                 "No   = overwrite those cells" & vbCrLf & _
                 "Cancel = stop", vbYesNoCancel + vbQuestion, APP_NAME)
    Select Case ans
        Case vbYes
            ws.Range(ws.Cells(1, col + 1), ws.Cells(1, col + nOut)).EntireColumn.Insert Shift:=xlToRight
            PrepareOutputArea = True
        Case vbNo
            With ws.Range(ws.Cells(topRow, col + 1), ws.Cells(lastRow, col + nOut))
                .ClearContents
                .ClearFormats
            End With
            PrepareOutputArea = True
        Case Else
            PrepareOutputArea = False
    End Select
End Function

Private Sub FormatOutputColumns(ByVal ws As Worksheet, ByVal col As Long, _
        ByVal firstRow As Long, ByVal lastRow As Long, ByVal headers As Variant)
    Dim i As Long, rng As Range
    For i = LBound(headers) To UBound(headers)
        Set rng = ws.Range(ws.Cells(firstRow, col + 1 + i), ws.Cells(lastRow, col + 1 + i))
        Select Case headers(i)
            Case H_LAT, H_TTL
                rng.NumberFormat = "General"
                rng.HorizontalAlignment = xlRight
            Case Else
                rng.NumberFormat = "@"
        End Select
    Next i
End Sub

Private Function ResultToRow(ByRef res As HostResult, ByVal doDns As Boolean, ByVal doPing As Boolean) As Variant()
    Dim v() As Variant
    If doDns And doPing Then
        ReDim v(0 To 6)
        v(0) = res.InputType: v(1) = res.Addresses: v(2) = res.Ptr: v(3) = res.Match
        v(4) = res.PingStatus: v(5) = res.Latency: v(6) = res.Ttl
    ElseIf doDns Then
        ReDim v(0 To 3)
        v(0) = res.InputType: v(1) = res.Addresses: v(2) = res.Ptr: v(3) = res.Match
    Else
        ReDim v(0 To 3)
        v(0) = res.FirstAddr: v(1) = res.PingStatus: v(2) = res.Latency: v(3) = res.Ttl
    End If
    ResultToRow = v
End Function

Private Sub ColourRow(ByVal ws As Worksheet, ByVal r As Long, ByVal col As Long, _
        ByVal headers As Variant, ByRef res As HostResult)
    Dim i As Long, cell As Range
    For i = LBound(headers) To UBound(headers)
        Set cell = ws.Cells(r, col + 1 + i)
        Select Case headers(i)
            Case H_PING
                If res.PingUp Then Good cell Else Bad cell
            Case H_MATCH
                If res.Match = "Yes" Then
                    Good cell
                ElseIf Left$(res.Match, 2) = "No" Then
                    Warn cell
                End If
            Case H_IPS, H_PINGIP
                If res.DnsFailed Then Bad cell
        End Select
    Next i
End Sub

Private Sub Good(ByVal c As Range)
    c.Interior.Color = RGB(198, 239, 206): c.Font.Color = RGB(0, 97, 0)
End Sub

Private Sub Bad(ByVal c As Range)
    c.Interior.Color = RGB(255, 199, 206): c.Font.Color = RGB(156, 0, 6)
End Sub

Private Sub Warn(ByVal c As Range)
    c.Interior.Color = RGB(255, 235, 156): c.Font.Color = RGB(156, 101, 0)
End Sub

'==============================================================================
' Clear Results
'==============================================================================
Private Sub ClearResults()
    Dim ws As Worksheet, col As Long, firstRow As Long, lastRow As Long, headerRow As Long
    Dim c As Long, n As Long

    If TypeName(Selection) <> "Range" Then Exit Sub
    Set ws = ActiveSheet
    If Not GetInputRows(ws, Selection, col, firstRow, lastRow, headerRow) Then Exit Sub

    If headerRow = 0 And firstRow > 1 Then headerRow = firstRow - 1
    If headerRow > 0 Then
        c = col + 1
        Do While IsNetToolsHeader(CStr(ws.Cells(headerRow, c).Value))
            n = n + 1
            c = c + 1
        Loop
    End If
    If n = 0 Then
        MsgBox "No NetTools results found next to the selected column." & vbCrLf & _
               "(Select the column of hosts that you ran the check on.)", vbInformation, APP_NAME
        Exit Sub
    End If

    If MsgBox("Clear " & n & " NetTools result column(s) to the right of column " & _
              ColumnLetter(col) & "?", vbOKCancel + vbQuestion, APP_NAME) <> vbOK Then Exit Sub

    With ws.Range(ws.Cells(headerRow, col + 1), ws.Cells(lastRow, col + n))
        .ClearContents
        .ClearFormats
    End With
End Sub

Private Function ColumnLetter(ByVal col As Long) As String
    ColumnLetter = Split(ActiveSheet.Cells(1, col).Address(True, False), "$")(0)
End Function

'==============================================================================
' Checking one host
'==============================================================================
Private Function CheckHost(ByVal host As String, ByVal doDns As Boolean, ByVal doPing As Boolean) As HostResult
    Dim res As HostResult, addrs As Collection, fwd As Collection, canonical As String
    Dim lat As Variant, ttl As Variant

    res.Latency = Empty
    res.Ttl = Empty
    canonical = NumericAddress(host)

    If Len(canonical) > 0 Then
        ' ---- Input is an IP address ----
        res.InputType = "IP"
        res.Addresses = canonical
        res.FirstAddr = canonical
        If doDns Then
            res.Ptr = ReverseLookup(canonical)
            If Len(res.Ptr) = 0 Then
                res.Ptr = "(no PTR record)"
                res.Match = "No PTR"
            Else
                ' Forward-confirm: does the PTR name resolve back to this IP?
                Set fwd = LookupAddresses(res.Ptr)
                If fwd.Count = 0 Then
                    res.Match = "No - PTR name does not resolve"
                ElseIf InCollection(fwd, canonical) Then
                    res.Match = "Yes"
                Else
                    res.Match = "No - PTR name resolves to " & JoinCollection(fwd)
                End If
            End If
        End If
    Else
        ' ---- Input is a hostname ----
        res.InputType = "Hostname"
        Set addrs = LookupAddresses(host)
        If addrs.Count = 0 Then
            res.DnsFailed = True
            res.Addresses = "(not found)"
            res.FirstAddr = "(not found)"
            res.Ptr = ""
            res.Match = "n/a"
            res.PingStatus = "Not resolved"
            CheckHost = res
            Exit Function
        End If
        res.Addresses = JoinCollection(addrs)
        res.FirstAddr = addrs(1)
        If doDns Then
            res.Ptr = ReverseLookup(res.FirstAddr)
            If Len(res.Ptr) = 0 Then
                res.Ptr = "(no PTR record)"
                res.Match = "No PTR"
            ElseIf NamesMatch(host, res.Ptr) Then
                res.Match = "Yes"
            Else
                res.Match = "No - PTR is a different name"
            End If
        End If
    End If

    If doPing Then
        res.PingStatus = PingHost(res.FirstAddr, lat, ttl)
        res.PingUp = (res.PingStatus = "Up")
        res.Latency = lat
        res.Ttl = ttl
    End If

    CheckHost = res
End Function

' "server1" matches "server1.corp.example.com"; full names must match exactly.
Private Function NamesMatch(ByVal a As String, ByVal b As String) As Boolean
    a = LCase$(a): b = LCase$(b)
    If Right$(a, 1) = "." Then a = Left$(a, Len(a) - 1)
    If Right$(b, 1) = "." Then b = Left$(b, Len(b) - 1)
    If a = b Then NamesMatch = True: Exit Function
    If InStr(a, ".") = 0 Then
        NamesMatch = (a = Split(b, ".")(0))
    ElseIf InStr(b, ".") = 0 Then
        NamesMatch = (b = Split(a, ".")(0))
    End If
End Function

' Tidies what's in the cell: trims, drops URL scheme, path, CIDR suffix, IPv6 brackets.
Private Function CleanHost(ByVal v As Variant) As String
    Dim s As String, p As Long
    If IsError(v) Or IsEmpty(v) Then Exit Function
    s = Trim$(CStr(v))
    s = Replace(s, vbTab, "")
    s = Replace(s, Chr$(160), "")
    s = Trim$(s)
    p = InStr(s, "://")
    If p > 0 Then s = Mid$(s, p + 3)
    p = InStr(s, "/")
    If p > 0 Then s = Left$(s, p - 1)
    If Left$(s, 1) = "[" Then
        p = InStr(s, "]")
        If p > 0 Then s = Mid$(s, 2, p - 2)
    End If
    p = InStr(s, " ")
    If p > 0 Then s = Left$(s, p - 1)
    CleanHost = s
End Function

'==============================================================================
' DNS (Windows resolver via ws2_32)
'==============================================================================
Private Function WsaInit() As Boolean
    Dim wsaData(0 To 511) As Byte
    WsaInit = (WSAStartup(&H202, wsaData(0)) = 0)
End Function

Private Function ReadLong(ByVal p As LongPtr) As Long
    Dim v As Long
    CopyMemory v, p, 4
    ReadLong = v
End Function

Private Function ReadPtr(ByVal p As LongPtr) As LongPtr
    Dim v As LongPtr
    CopyMemory v, p, PTR_SIZE
    ReadPtr = v
End Function

Private Function TrimNull(ByVal s As String) As String
    Dim p As Long
    p = InStr(s, vbNullChar)
    If p > 0 Then TrimNull = Left$(s, p - 1) Else TrimNull = s
End Function

Private Function SockaddrToString(ByVal sa As LongPtr, ByVal saLen As Long, ByVal flags As Long) As String
    Dim buf As String
    If sa = 0 Then Exit Function
    buf = String$(NI_MAXHOST, vbNullChar)
    If getnameinfo(sa, saLen, buf, NI_MAXHOST, 0, 0, flags) = 0 Then
        SockaddrToString = TrimNull(buf)
    End If
End Function

' Returns the canonical text form if s is an IP address literal, else "".
Private Function NumericAddress(ByVal s As String) As String
    Dim hints(0 To 11) As Long, res As LongPtr
    hints(0) = AI_NUMERICHOST
    hints(2) = SOCK_STREAM
    If getaddrinfo(s, 0, VarPtr(hints(0)), res) = 0 Then
        If res <> 0 Then
            NumericAddress = SockaddrToString(ReadPtr(res + AI_OFS_ADDR), ReadLong(res + AI_OFS_ADDRLEN), NI_NUMERICHOST)
            freeaddrinfo res
        End If
    End If
End Function

' Forward lookup. Returns every address, IPv4 first, then IPv6.
Private Function LookupAddresses(ByVal host As String) As Collection
    Dim hints(0 To 11) As Long, res As LongPtr, p As LongPtr
    Dim v4 As New Collection, v6 As New Collection, all As New Collection
    Dim fam As Long, ip As String, item As Variant

    hints(2) = SOCK_STREAM          ' one entry per address instead of per socket type
    If getaddrinfo(host, 0, VarPtr(hints(0)), res) = 0 Then
        p = res
        Do While p <> 0
            fam = ReadLong(p + AI_OFS_FAMILY)
            ip = SockaddrToString(ReadPtr(p + AI_OFS_ADDR), ReadLong(p + AI_OFS_ADDRLEN), NI_NUMERICHOST)
            If Len(ip) > 0 Then
                If fam = AF_INET Then
                    AddUnique v4, ip
                ElseIf fam = AF_INET6 Then
                    AddUnique v6, ip
                End If
            End If
            p = ReadPtr(p + AI_OFS_NEXT)
        Loop
        If res <> 0 Then freeaddrinfo res
    End If

    For Each item In v4: all.Add item: Next item
    For Each item In v6: all.Add item: Next item
    Set LookupAddresses = all
End Function

' Reverse (PTR) lookup for an IP address. "" if there is no record.
Private Function ReverseLookup(ByVal ip As String) As String
    Dim hints(0 To 11) As Long, res As LongPtr
    hints(0) = AI_NUMERICHOST
    hints(2) = SOCK_STREAM
    If getaddrinfo(ip, 0, VarPtr(hints(0)), res) = 0 Then
        If res <> 0 Then
            ReverseLookup = SockaddrToString(ReadPtr(res + AI_OFS_ADDR), ReadLong(res + AI_OFS_ADDRLEN), NI_NAMEREQD)
            freeaddrinfo res
        End If
    End If
End Function

Private Sub AddUnique(ByVal c As Collection, ByVal s As String)
    If Not InCollection(c, s) Then c.Add s
End Sub

Private Function InCollection(ByVal c As Collection, ByVal s As String) As Boolean
    Dim item As Variant
    For Each item In c
        If StrComp(CStr(item), s, vbTextCompare) = 0 Then InCollection = True: Exit Function
    Next item
End Function

Private Function JoinCollection(ByVal c As Collection) As String
    Dim item As Variant, s As String
    For Each item In c
        If Len(s) > 0 Then s = s & ", "
        s = s & CStr(item)
    Next item
    JoinCollection = s
End Function

'==============================================================================
' Ping (WMI Win32_PingStatus)
'==============================================================================
Private Function PingHost(ByVal addr As String, ByRef latency As Variant, ByRef ttl As Variant) As String
    Dim replies As Object, reply As Object, code As Variant
    latency = Empty
    ttl = Empty
    On Error GoTo Fail

    If mWmi Is Nothing Then
        Set mWmi = GetObject("winmgmts:{impersonationLevel=impersonate}!\\.\root\cimv2")
    End If
    Set replies = mWmi.ExecQuery( _
        "SELECT StatusCode, ResponseTime, ResponseTimeToLive FROM Win32_PingStatus " & _
        "WHERE Address='" & Replace(addr, "'", "") & "' AND Timeout=" & PING_TIMEOUT_MS)

    PingHost = "No response"
    For Each reply In replies
        code = reply.StatusCode
        If IsNull(code) Then
            PingHost = "No response"
        ElseIf CLng(code) = 0 Then
            PingHost = "Up"
            latency = reply.ResponseTime
            If Not IsNull(reply.ResponseTimeToLive) Then ttl = reply.ResponseTimeToLive
        Else
            PingHost = PingStatusText(CLng(code))
        End If
    Next reply
    Exit Function

Fail:
    If Err.Number = 18 Then
        Err.Raise 18     ' Esc pressed - let RunChecks stop
    End If
    PingHost = "Error: " & Err.Description
End Function

Private Function PingStatusText(ByVal code As Long) As String
    Select Case code
        Case 11002: PingStatusText = "Destination net unreachable"
        Case 11003: PingStatusText = "Destination host unreachable"
        Case 11004: PingStatusText = "Destination protocol unreachable"
        Case 11005: PingStatusText = "Destination port unreachable"
        Case 11010: PingStatusText = "Timed out"
        Case 11013: PingStatusText = "TTL expired in transit"
        Case 11018: PingStatusText = "Bad destination"
        Case 11050: PingStatusText = "General failure"
        Case Else:  PingStatusText = "Failed (" & code & ")"
    End Select
End Function

Private Function ElapsedSeconds(ByVal t0 As Double) As Double
    Dim t As Double
    t = Timer - t0
    If t < 0 Then t = t + 86400#     ' ran past midnight
    ElapsedSeconds = t
End Function

'==============================================================================
' Worksheet functions
'==============================================================================

' =NT_RESOLVE("server1")  ->  "10.1.2.3"   (all addresses, comma separated)
Public Function NT_RESOLVE(ByVal host As String) As String
    Dim addrs As Collection
    host = CleanHost(host)
    If Len(host) = 0 Then Exit Function
    If Not WsaInit() Then NT_RESOLVE = "#WSA error": Exit Function
    Set addrs = LookupAddresses(host)
    If addrs.Count = 0 Then NT_RESOLVE = "(not found)" Else NT_RESOLVE = JoinCollection(addrs)
    WSACleanup
End Function

' =NT_PTR("10.1.2.3")  ->  "server1.corp.example.com"
' A hostname is resolved first and its first address looked up.
Public Function NT_PTR(ByVal hostOrIp As String) As String
    Dim ip As String, addrs As Collection
    hostOrIp = CleanHost(hostOrIp)
    If Len(hostOrIp) = 0 Then Exit Function
    If Not WsaInit() Then NT_PTR = "#WSA error": Exit Function
    ip = NumericAddress(hostOrIp)
    If Len(ip) = 0 Then
        Set addrs = LookupAddresses(hostOrIp)
        If addrs.Count > 0 Then ip = addrs(1)
    End If
    If Len(ip) = 0 Then
        NT_PTR = "(not found)"
    Else
        NT_PTR = ReverseLookup(ip)
        If Len(NT_PTR) = 0 Then NT_PTR = "(no PTR record)"
    End If
    WSACleanup
End Function

' =NT_PING("server1")  ->  "Up (3 ms)" / "Timed out" / "Not resolved"
Public Function NT_PING(ByVal host As String) As String
    Dim ip As String, addrs As Collection, lat As Variant, ttl As Variant, status As String
    host = CleanHost(host)
    If Len(host) = 0 Then Exit Function
    If Not WsaInit() Then NT_PING = "#WSA error": Exit Function
    ip = NumericAddress(host)
    If Len(ip) = 0 Then
        Set addrs = LookupAddresses(host)
        If addrs.Count > 0 Then ip = addrs(1)
    End If
    WSACleanup
    If Len(ip) = 0 Then NT_PING = "Not resolved": Exit Function
    status = PingHost(ip, lat, ttl)
    If status = "Up" Then NT_PING = "Up (" & lat & " ms)" Else NT_PING = status
End Function
