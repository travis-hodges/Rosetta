ROSREC ;Example only: recording a visit bumps the per-patient counter
RECORD(ID)
 S ^VISITS(ID)=$G(^VISITS(ID))+1
 W "RECORDED",!
 Q
