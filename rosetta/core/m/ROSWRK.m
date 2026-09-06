ROSWRK ;Rosetta;Long-lived verification worker
 ;
 ; Driven by rosetta.core.worker over a docker-exec pipe. One process serves
 ; many cases: `docker exec` + `mumps -run` costs ~448ms and would dominate
 ; every cycle.
 ;
 ; LINE PROTOCOL ($PRINCIPAL only; nothing else is ever written there)
 ;   request  : KEY<TAB>VALUE lines, terminated by a bare line "END"
 ;   response : KEY<TAB>VALUE lines, terminated by a bare line "END"
 ;   VALUE is percent-escaped:  %->%25  =->%3D  TAB->%09  LF->%0A  CR->%0D
 ;
 ; ERROR DISCIPLINE
 ;   $ZTRAP uses ZGOTO <level>:<label> so an error unwinds deterministically to
 ;   a known frame instead of leaving the process in direct mode (which would
 ;   hang the pipe and then NOPRINCIO). The fatal handler disarms itself first,
 ;   so it can never recurse; a nested error halts and the supervisor respawns.
 ;
 Q  ; not callable at the top
 ;
 ;----------------------------------------------------------------- main loop
MAIN ; mumps -run MAIN^ROSWRK
 N ROSLVL,ROSQ,ROSST,ROSREQ,ROSRES,ROSTMPD,ROSCAPF,ROSOBJD
 S U="^"
 U $P:(NOWRAP:WIDTH=1048576)
 S ROSTMPD=$ZTRNLNM("ROSWRKTMP")
 I ROSTMPD="" S ROSTMPD="/home/vehu/tmp/rosetta"
 S ROSOBJD=$ZTRNLNM("ROSWRKOBJD")
 ; Per-process capture file. A plain OS file gets no TP isolation, so a
 ; shared path let concurrent verifiers read each other's stdout and
 ; return a confident verdict for somebody else's candidate.
 S ROSCAPF=ROSTMPD_"/capture."_$J_".txt"
 S ROSLVL=$ZLEVEL,ROSQ=0,ROSST=""
 W "ROSETTA-WORKER",$C(9),"1",!
MLOOP ; one request/response round trip
 S $ZTRAP="S ROSST=$ZSTATUS ZGOTO "_ROSLVL_":MFATAL^ROSWRK"
 K ROSRES
 D DISPATCH
 D FLUSH
 G:'ROSQ MLOOP
 H
 ;
MFATAL ; an error escaped a handler: disarm, report, resume the loop
 S $ZTRAP="H"                        ; non-recursive: a nested error kills us
 I $TLEVEL>0 TROLLBACK
 K ROSRES
 D RESP("STATUS","ERR")
 D RESP("ERROR",$$MERR($G(ROSST)))
 D RESP("ZSTATUS",$G(ROSST))
 D FLUSH
 S ROSST=""
 G MLOOP
 ;
 ;----------------------------------------------------------------- dispatch
DISPATCH N CMD
 K ROSREQ
 D READREQ
 Q:ROSQ
 S CMD=$$RQ("CMD")
 I CMD="PING" D RESP("STATUS","OK"),RESP("PONG","1") Q
 I CMD="HALT" S ROSQ=1 D RESP("STATUS","OK") Q
 I CMD="LINK" D LINK Q
 I CMD="EXEC" D EXEC Q
 I CMD="TRIG" D TRIG Q
 I CMD="COUNT" D COUNT Q
 I CMD="GETG" D GETG Q
 I CMD="APPLYG" D APPLYG Q
 I CMD="FILEMAN" D FILEMAN Q
 I CMD="KILLG" D KILLG Q
 I CMD="RESET" D RESET Q
 D RESP("STATUS","ERR"),RESP("ERROR","unknown command: "_CMD)
 Q
 ;
READREQ ; read one request into ROSREQ(key)=count, ROSREQ(key,n)=raw value
 N L,K,V,N,DONE
 S DONE=0
 F  Q:DONE  D
 . R L:600
 . I '$T S ROSQ=1,DONE=1 Q
 . I $ZEOF S ROSQ=1,DONE=1 Q
 . I L="END" S DONE=1 Q
 . I L="" Q
 . S K=$P(L,$C(9),1),V=$P(L,$C(9),2,999999)
 . S N=$G(ROSREQ(K))+1,ROSREQ(K)=N,ROSREQ(K,N)=V
 Q
 ;
RQ(K,N) ; unescaped single request value
 Q $$UNESC($G(ROSREQ(K,$G(N,1))))
 ;
RESP(K,V) ; queue one response line
 N N S N=$G(ROSRES)+1,ROSRES=N,ROSRES(N)=K_$C(9)_$$ESC(V)
 Q
 ;
FLUSH N I
 S I="" F  S I=$O(ROSRES(I)) Q:I=""  W ROSRES(I),!
 W "END",!
 K ROSRES
 Q
 ;
 ;----------------------------------------------------------------- escaping
ESC(S) N I,C,O
 Q:$L(S)=0 ""
 I S'["%",S'["=",$TR(S,$C(9,10,13),"")=S Q S
 S O=""
 F I=1:1:$L(S) S C=$E(S,I) S O=O_$S(C="%":"%25",C="=":"%3D",C=$C(9):"%09",C=$C(10):"%0A",C=$C(13):"%0D",1:C)
 Q O
 ;
UNESC(S) N I,P,O
 Q:S'["%" S
 S O=$P(S,"%",1)
 F I=2:1:$L(S,"%") S P=$P(S,"%",I),O=O_$C($$HX($E(P,1,2)))_$E(P,3,$L(P))
 Q O
 ;
HX(H) N D,I,V
 S D="0123456789ABCDEF",V=0
 F I=1:1:$L(H) S V=(V*16)+($F(D,$ZCONVERT($E(H,I),"U"))-2)
 Q V
 ;
MERR(ST) ; "%YDB-E-LVUNDEF, Undefined local variable: X" -> "LVUNDEF, Undefined..."
 N P
 S P=$P(ST,",",2,999)
 S P=$S(P="":ST,1:$P($P(ST,",",1),"-",3)_","_P)
 Q P
 ;
 ;----------------------------------------------------------------- commands
LINK ; recompile and link one routine from source
 N R,LVL
 S R=$$RQ("ROUTINE")
 I R="" D RESP("STATUS","ERR"),RESP("ERROR","LINK: no ROUTINE") Q
 S LVL=$ZLEVEL
 S $ZTRAP="S ROSST=$ZSTATUS ZGOTO "_LVL_":LINKERR^ROSWRK"
 ; Drop any existing object first. A stale or partially written .o makes the
 ; NEXT ZLINK of this routine fail with INVOBJFILE ("unexpected format") even
 ; when the source is byte-identical to one that just linked cleanly, which
 ; rejected 561 of 565 candidates in a real benchmark build. Compiling from
 ; source every time costs milliseconds and removes the whole failure class.
 D DROPOBJ(R)
 ZLINK R_".m"
 ; ZLINK prints compiler diagnostics to stderr but does NOT raise a trappable
 ; error, so $ZTRAP never fires on a syntax error. $ZCSTATUS is 1 after a clean
 ; compile and carries the error code otherwise. Without this check a mutant
 ; that does not compile would be admitted to the benchmark (section 9 rule 2).
 I $ZCSTATUS'=1 D RESP("STATUS","ERR"),RESP("ERROR","compile failed: "_$ZMESSAGE($ZCSTATUS)),RESP("ZSTATUS",$ZCSTATUS) Q
 D RESP("STATUS","OK")
 Q
DROPOBJ(R) ; delete this routine's object file from our private object dir
 N D,F,E
 S D=$G(ROSOBJD) Q:D=""
 S F=D_"/"_R_".o"
 S E=$ZTRAP,$ZTRAP="S $EC="""" Q"   ; a missing object is the normal case
 O F:(NEWVERSION:EXCEPTION="S $EC="""" Q") C F:DELETE
 S $ZTRAP=E
 Q
 ;
LINKERR D RESP("STATUS","ERR"),RESP("ERROR",$$MERR($G(ROSST))),RESP("ZSTATUS",$G(ROSST))
 Q
 ;
KILLG ; KILL global roots outside any transaction
 N I,R
 I $TLEVEL>0 TROLLBACK
 F I=1:1:+$G(ROSREQ("REF")) S R=$$RQ("REF",I) I R'="" K @R
 D RESP("STATUS","OK")
 Q
 ;
GETG ; read exact global nodes outside a transaction
 N I,R,P
 I $TLEVEL>0 TROLLBACK
 F I=1:1:+$G(ROSREQ("REF")) D
 . S R=$$RQ("REF",I),P=$D(@R)#10
 . D RESP("GR",R),RESP("GP",P),RESP("GV",$S(P:$G(@R),1:""))
 D RESP("STATUS","OK")
 Q
 ;
APPLYG ; atomically commit exact global-node mutations
 N I,N,R,OP,V,BP,BV,P,BAD
 I $TLEVEL>0 TROLLBACK
 S N=+$G(ROSREQ("REF")),BAD=0
 I N<1 D RESP("STATUS","ERR"),RESP("ERROR","APPLYG: no operations") Q
 ; Check every optimistic-lock precondition before changing anything.
 F I=1:1:N D
 . S R=$$RQ("REF",I),OP=$$RQ("OP",I),BP=+$$RQ("BEFOREP",I),BV=$$RQ("BEFOREV",I),P=$D(@R)#10
 . I OP'="SET",OP'="KILL" S BAD=1 D RESP("CONFLICT",R_" (invalid operation)") Q
 . I P'=BP S BAD=1 D RESP("CONFLICT",R_" (presence changed)") Q
 . I P,$G(@R)'=BV S BAD=1 D RESP("CONFLICT",R_" (value changed)") Q
 . I OP="KILL",$D(@R)>1 S BAD=1 D RESP("CONFLICT",R_" (has descendants; subtree deletion refused)")
 I BAD D RESP("STATUS","CONFLICT") Q
 ; No customer routine runs here. These literal SET/KILL operations are one
 ; atomic commit, and Python has already captured a full MUPIP snapshot.
 TSTART ():SERIAL
 F I=1:1:N D
 . S R=$$RQ("REF",I),OP=$$RQ("OP",I),V=$$RQ("VALUE",I)
 . I OP="SET" S @R=V Q
 . K @R
 TCOMMIT
 F I=1:1:N D
 . S R=$$RQ("REF",I),P=$D(@R)#10
 . D RESP("GR",R),RESP("GP",P),RESP("GV",$S(P:$G(@R),1:""))
 D RESP("STATUS","OK")
 Q
 ;
FILEMAN ; create or update one top-level record through supported DBS APIs
 N FILE,IENS,I,N,FLD,VAL,NEWIEN,ERR,P,LVL,FDA,DUZ,DT
 I $TLEVEL>0 TROLLBACK
 S FILE=$$RQ("FILE"),IENS=$$RQ("IENS"),N=+$G(ROSREQ("FIELD"))
 I FILE'>0!(IENS="")!(N<1) D RESP("STATUS","ERR"),RESP("ERROR","FILEMAN: file, IENS, and fields are required") Q
 ; FDA subscripts are data, never executable text. Python validates each one.
 F I=1:1:N S FLD=$$RQ("FIELD",I),VAL=$$RQ("VALUE",I),FDA(FILE,IENS,FLD)=VAL
 ; Minimal programmer context for this local proof-of-concept instance.
 I '$D(DUZ) S DUZ=.5
 S DUZ(0)="@",DT=$$DT^XLFDT
 S LVL=$ZLEVEL
 S $ZTRAP="S ROSST=$ZSTATUS ZGOTO "_LVL_":FMERR^ROSWRK"
 I $E(IENS)="+" D UPDATE^DIE("E","FDA","NEWIEN","ERR") I '$D(ERR) S IENS=+$G(NEWIEN(1))_","
 E  D FILE^DIE("ET","FDA","ERR")
 I $D(ERR("DIERR")) D RESP("STATUS","ERR"),RESP("ERROR",$S($G(ERR("DIERR",1,"TEXT",1))'="":ERR("DIERR",1,"TEXT",1),1:"FileMan rejected the change")) Q
 I +IENS'>0 D RESP("STATUS","ERR"),RESP("ERROR","FileMan did not return an IEN") Q
 D RESP("IEN",+IENS)
 F I=1:1:N S FLD=$$RQ("FIELD",I) D RESP("FIELD",FLD),RESP("VALUE",$$GET1^DIQ(FILE,IENS,FLD,"E"))
 D RESP("STATUS","OK")
 Q
FMERR D RESP("STATUS","ERR"),RESP("ERROR",$$MERR($G(ROSST))),RESP("ZSTATUS",$G(ROSST))
 Q
 ;
RESET ; return the process to a known state
 I $TLEVEL>0 TROLLBACK
 K ^ROSLOG($J),^ROSTMP($J)
 D RESP("STATUS","OK"),RESP("TLEVEL",$TLEVEL)
 Q
 ;
COUNT ; node count under each ROOT, capped at MAX (capture planning)
 N I,R,MAX,C,K
 S MAX=+$$RQ("MAX") S:MAX'>0 MAX=100000
 I $TLEVEL>0 TROLLBACK
 F I=1:1:+$G(ROSREQ("ROOT")) D
 . S R=$$RQ("ROOT",I) Q:R=""
 . S C=0,K=R
 . I $D(@R)#10 S C=1
 . F  S K=$Q(@K) Q:K=""  Q:$E(K,1,$L(R)+1)'=(R_"(")  S C=C+1 Q:C>MAX
 . D RESP("N",R_"="_C)
 D RESP("STATUS","OK"),RESP("MAX",MAX)
 Q
 ;
TRIG ; install or clear $ZTRIGGER capture triggers
 ; MODE=INSTALL|CLEAR, ROOT (repeated), DEPTH=n
 ; Triggers are per (global, subscript depth): a "(*)" spec matches exactly one
 ; subscript level, so one pair of triggers is installed per depth 0..DEPTH.
 ; $ZTRIGGER inside TP silently no-ops, so refuse to run in a transaction.
 N MODE,I,R,D,SUB,RC,OK,BAD,PFX,SX,KX
 S MODE=$$RQ("MODE")
 S PFX=$$RQ("PREFIX")
 I PFX'?1"Ros"16AN D RESP("STATUS","ERR"),RESP("ERROR","invalid trigger owner prefix") Q
 I $TLEVEL>0 D RESP("STATUS","ERR"),RESP("ERROR","TRIG inside TP ($TLEVEL="_$TLEVEL_")") Q
 S OK=0,BAD=0
 I MODE="CLEAR" D  Q
 . N X S X=$ZTRIGGER("ITEM","-"_PFX_"*")
 . D RESP("STATUS","OK"),RESP("CLEARED",X)
 S D=+$$RQ("DEPTH") S:D'>0 D=8
 ; Include the process ID in the trigger signature so another runtime cannot
 ; rename an equivalent trigger definition and accidentally assume ownership.
 S SX="I $J="_$J_" D TS^ROSWRK",KX="I $J="_$J_" D TK^ROSWRK"
 F I=1:1:+$G(ROSREQ("ROOT")) D
 . S R=$$RQ("ROOT",I) Q:R=""
 . S RC=$ZTRIGGER("ITEM","+"_R_" -commands=SET -xecute="""_SX_""" -name="_PFX_"S"_I_"D0")
 . S:RC OK=OK+1 S:'RC BAD=BAD+1
 . S RC=$ZTRIGGER("ITEM","+"_R_" -commands=KILL,ZKILL -xecute="""_KX_""" -name="_PFX_"K"_I_"D0")
 . S:RC OK=OK+1 S:'RC BAD=BAD+1
 . S SUB=""
 . N J F J=1:1:D D
 . . S SUB=$S(J=1:"*",1:SUB_",*")
 . . S RC=$ZTRIGGER("ITEM","+"_R_"("_SUB_") -commands=SET -xecute="""_SX_""" -name="_PFX_"S"_I_"D"_J)
 . . S:RC OK=OK+1 S:'RC BAD=BAD+1
 . . S RC=$ZTRIGGER("ITEM","+"_R_"("_SUB_") -commands=KILL,ZKILL -xecute="""_KX_""" -name="_PFX_"K"_I_"D"_J)
 . . S:RC OK=OK+1 S:'RC BAD=BAD+1
 D RESP("STATUS",$S(BAD=0:"OK",1:"ERR")),RESP("INSTALLED",OK),RESP("FAILED",BAD)
 Q
 ;
TS ; SET trigger body. Read $REFERENCE FIRST: our own log write clobbers it.
 N ROSR S ROSR=$REFERENCE
 N ROSN S ROSN=$I(^ROSLOG($J))
 S ^ROSLOG($J,ROSN)="S"_$C(9)_ROSR_$C(9)_$G(@ROSR)
 Q
 ;
TK ; KILL/ZKILL trigger body
 N ROSR S ROSR=$REFERENCE
 ; KILL only triggers at the target, not descendants. Refuse a subtree
 ; deletion whose removed descendants are outside the captured write log.
 I $D(@ROSR)>9 S ^ROSTMP($J,"TRUNC")=1
 N ROSN S ROSN=$I(^ROSLOG($J))
 S ^ROSLOG($J,ROSN)="K"_$C(9)_ROSR_$C(9)_""
 Q
 ;
 ;----------------------------------------------------------------- execution
EXEC ; run one ExecSpec inside a TP frame
 N ROU,ENTRY,ISX,NARG,I,K,V,LVL0,LVL,TL,T0,T1
 N ROSA,ROSCMD,ROSRV,ROSERR,ROSOUT,ROSGV,ROSRST,ROSVOID,ROSMARK,ROSCAP
 N ROSWROOT,ROSMAXN,ROSTRUNC,ROSSEED,NSEED,ROSREASON
 S ROU=$$RQ("ROUTINE")
 I ROU="" D RESP("STATUS","ERR"),RESP("ERROR","EXEC: no ROUTINE") Q
 S ENTRY=$$RQ("ENTRY")
 S ISX=+$$RQ("EXTRINSIC")
 S ROSCAP=+$$RQ("CAPOUT")
 S ROSMAXN=+$$RQ("MAXNODES") S:ROSMAXN'>0 ROSMAXN=20000
 ;
 ; locals_in
 F I=1:1:+$G(ROSREQ("LOCALN")) D
 . S K=$$RQ("LOCALN",I) Q:K=""
 . S @K=$$RQ("LOCALV",I)
 ; args
 S NARG=+$G(ROSREQ("ARG"))
 F I=1:1:NARG S ROSA(I)=$$RQ("ARG",I)
 ; watch roots for the $QUERY tier
 F I=1:1:+$G(ROSREQ("WATCH")) S ROSWROOT(I)=$$RQ("WATCH",I)
 ; globals_in, seeded INSIDE the frame so the rollback removes them
 S NSEED=+$G(ROSREQ("GLOBALR"))
 F I=1:1:NSEED S ROSSEED(I)=$$RQ("GLOBALR",I),ROSSEED(I,"v")=$$RQ("GLOBALV",I)
 ;
 S ROSCMD=$$MKCMD(ROU,ENTRY,ISX,NARG)
 S ROSERR="",ROSOUT="",ROSRST=0,ROSVOID=0,ROSTRUNC=0,ROSREASON="",ROSRV=""
 ;
 ; Hold ZERO M locks across TSTART: TPLOCK is a hard error.
 LOCK
 ;
 I ROSCAP O ROSCAPF:(NEWVERSION:VARIABLE:RECORDSIZE=1048576)
 ;
 S LVL0=$TLEVEL
 I LVL0'=0 TROLLBACK  S LVL0=$TLEVEL
 S ROSMARK="ROSETTA"
 S T0=$ZUT
 ;
 ; "*" restores the whole local symbol table on a restart. Anything not named
 ; is NOT restored, and a silent restart would otherwise leave stale capture.
 TSTART *:SERIAL
 K ^ROSLOG($J),^ROSTMP($J,"TRUNC")
 F I=1:1:NSEED S @ROSSEED(I)=ROSSEED(I,"v")
 S LVL=$ZLEVEL
 S $ZTRAP="S ROSERR=$ZSTATUS ZGOTO "_LVL_":BODYERR^ROSWRK"
 I ROSCAP U ROSCAPF
 XECUTE ROSCMD
 I ROSCAP U $P
 G BODYOK
BODYERR I $G(ROSCAP) U $P
 S ROSERR=$$MERR(ROSERR)
BODYOK S $ZTRAP="S ROSST=$ZSTATUS ZGOTO "_ROSLVL_":MFATAL^ROSWRK"
 S T1=$ZUT
 ;
 ; Frame integrity. An unbalanced TCOMMIT or a bare TROLLBACK in the code under
 ; test collapses $TLEVEL silently and every later write hits the live DB.
 I $G(ROSMARK)'="ROSETTA" D  Q
 . I $TLEVEL>0 TROLLBACK
 . W "STATUS",$C(9),"VOID",!,"REASON",$C(9),"symbol-table-wiped",!,"END",!
 . H
 S TL=$TLEVEL
 I TL'>LVL0 S ROSVOID=1,ROSREASON="tlevel-collapsed-to-"_TL
 ;
 I 'ROSVOID D
 . S ROSRST=$TRESTART
 . D HARVEST
 . TROLLBACK
 ;
 I ROSCAP C ROSCAPF D READCAP
 ;
 D RESP("STATUS",$S(ROSVOID:"VOID",1:"OK"))
 D RESP("VOID",+ROSVOID)
 D:ROSREASON'="" RESP("REASON",ROSREASON)
 D RESP("RESTARTS",ROSRST)
 D RESP("TLEVEL",$TLEVEL)
 D RESP("DURUS",T1-T0)
 D RESP("ERROR",ROSERR)
 D RESP("STDOUT",ROSOUT_ROSRV)
 D RESP("TRUNCATED",ROSTRUNC)
 I 'ROSVOID D
 . S K="" F  S K=$O(ROSGV(K)) Q:K=""  D RESP("GR",K),RESP("GV",ROSGV(K))
 Q
 ;
MKCMD(ROU,ENTRY,ISX,NARG) ; build the XECUTE string; args ride in ROSA(), never
 ; interpolated as literals, so quoting can never break out. An argument
 ; written ".NAME" is passed by reference to the local NAME the caller seeded
 ; through locals_in -- the only way to hand a routine an array like PXVSC's X.
 N A,I,T,V
 S A=""
 F I=1:1:NARG D
 . S V=ROSA(I)
 . I $E(V)=".",$E(V,2,$L(V))?1(1A,1"%").AN S A=A_$S(I=1:"",1:",")_V Q
 . S A=A_$S(I=1:"",1:",")_"ROSA("_I_")"
 I A'="" S A="("_A_")"
 S T=$S(ENTRY="":"",1:ENTRY)_"^"_ROU
 Q $S(ISX:"S ROSRV=$$"_T_A,1:"D "_T_A)
 ;
HARVEST ; collect globals_out while still inside the frame
 N I,R,C,K
 ; tier 2 -- final state at touched nodes within the configured depth bound
 I $G(^ROSTMP($J,"TRUNC")) S ROSTRUNC=ROSTRUNC+1,ROSREASON="trigger-subtree-deletion"
 S C="" F  S C=$O(^ROSLOG($J,C)) Q:C=""  D
 . S R=$P(^ROSLOG($J,C),$C(9),2)
 . S ROSGV(R)=$S($D(@R)#10:@R,1:$C(1)_"KILLED")
 ; tier 1 -- scoped $QUERY walk of statically-named roots
 S I="" F  S I=$O(ROSWROOT(I)) Q:I=""  D
 . S R=ROSWROOT(I) Q:R=""
 . S C=0
 . I $D(@R)#10 S ROSGV(R)=@R,C=1
 . S K=R
 . F  S K=$Q(@K) Q:K=""  Q:$E(K,1,$L(R)+1)'=(R_"(")  S ROSGV(K)=@K,C=C+1 Q:C>ROSMAXN
 . I C>ROSMAXN S ROSTRUNC=ROSTRUNC+1
 Q
 ;
READCAP ; slurp the capture file. Device output is NOT rolled back, so this is
 ; read after TROLLBACK; NEWVERSION at OPEN truncates on every case, and a
 ; silent restart is discarded by the caller on RESTARTS>0. Lines are rejoined
 ; with LF and no trailing LF is added -- a trailing newline in device output
 ; is normalised away, identically for baseline and candidate.
 N L,N,LN
 S ROSOUT="",N=0
 O ROSCAPF:(READONLY:VARIABLE:RECORDSIZE=1048576:EXCEPTION="G RCDONE^ROSWRK")
 U ROSCAPF
 F  R L:5 Q:$ZEOF  S N=N+1,LN(N)=L Q:N>100000
RCDONE U $P
 C ROSCAPF
 N I S I="" F  S I=$O(LN(I)) Q:I=""  S ROSOUT=ROSOUT_$S(I=1:"",1:$C(10))_LN(I)
 Q
