/* Rigid node animation of source-family meshes; no scripts or network per frame. */
function nativeModelPose(model,frame) {
  const animation=model.animation,matrices=[],pending=new Set(animation.nodes.map((_,i)=>i));
  const multiply=(a,b)=>Array.from({length:16},(_,i)=>{let value=0;for(let k=0;k<4;k++)value+=a[Math.floor(i/4)*4+k]*b[k*4+i%4];return value;});
  while(pending.size){let progress=false;for(const node of pending){const parent=animation.parents[node];if(parent!=null && !matrices[parent])continue;
    const track=animation.nodes[node],[x,y,z,w]=track.rotations[frame],p=track.positions[frame];
    const local=[w*w+x*x-y*y-z*z,2*x*y-2*w*z,2*x*z+2*w*y,p[0],2*x*y+2*w*z,w*w-x*x+y*y-z*z,2*y*z-2*w*x,p[1],2*x*z-2*w*y,2*y*z+2*w*x,w*w-x*x-y*y+z*z,p[2],0,0,0,1];
    matrices[node]=parent==null ? local : multiply(matrices[parent],local);pending.delete(node);progress=true;
  }if(!progress)throw Error('Animation hierarchy has a cycle or missing parent.');}
  const vertices=model.vertices.map(v=>[...v]);
  for(const part of model.parts){const matrix=matrices[part.node];for(let i=part.start;i<part.start+part.vertices;i++){const p=model.vertices[i];vertices[i]=[0,1,2].map(k=>matrix[k*4]*p[0]+matrix[k*4+1]*p[1]+matrix[k*4+2]*p[2]+matrix[k*4+3]);}}
  return vertices;
}
function setupNativeModelPlayback(host,model,active,update) {
  if(!model.animation)return ()=>{};
  const controls=document.createElement('div');controls.className='row';
  const play=document.createElement('button');play.type='button';play.className='primary small-btn';play.textContent='Play animation';
  const label=document.createElement('label');label.className='field';const title=document.createElement('span');title.textContent='Preview frames per second (1–60)';const rate=document.createElement('input');rate.type='number';rate.min=1;rate.max=60;rate.value=15;label.append(title,rate);
  const frame=document.createElement('input');frame.type='range';frame.min=0;frame.max=model.animation.frames-1;frame.value=0;frame.setAttribute('aria-label','Animation frame');
  const status=document.createElement('span');status.className='small muted';status.setAttribute('aria-live','polite');controls.append(play,label,frame,status);host.append(controls);
  let running=false,request=null,last=0,current=0,disposed=false;
  const draw=n=>{current=n;frame.value=n;status.textContent=`Frame ${n+1} / ${model.animation.frames}`;update(nativeModelPose(model,n));};
  const stop=()=>{running=false;if(request!=null)cancelAnimationFrame(request);request=null;play.textContent='Play animation';rate.disabled=false;};
  const tick=time=>{if(disposed || !running || !active() || !host.isConnected || document.hidden){stop();return;}const interval=1000/Math.max(1,Math.min(60,Number(rate.value)||15));if(time-last>=interval){const steps=Math.max(1,Math.floor((time-last)/interval));last=time;draw((current+steps)%model.animation.frames);}request=requestAnimationFrame(tick);};
  play.onclick=()=>{if(disposed || !active())return;if(running){stop();return;}running=true;play.textContent='Pause animation';rate.disabled=true;last=performance.now();request=requestAnimationFrame(tick);};
  frame.oninput=()=>{stop();draw(Number(frame.value));};
  const hide=()=>{if(document.hidden)stop();};document.addEventListener('visibilitychange',hide);
  draw(0);
  return ()=>{disposed=true;stop();document.removeEventListener('visibilitychange',hide);};
}
