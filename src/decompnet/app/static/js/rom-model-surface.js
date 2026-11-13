/* A depth-tested local viewer. No remote textures, runtime scripts or dependencies. */
function createRomModelSurface(canvas,model,active,refresh) {
  const gl=canvas.getContext('webgl',{alpha:false,antialias:true});if(!gl)return null;
  const objects=[],textures=[],images=[];
  const shader=(type,source)=>{const s=gl.createShader(type);gl.shaderSource(s,source);gl.compileShader(s);if(!gl.getShaderParameter(s,gl.COMPILE_STATUS)){gl.deleteShader(s);throw Error('The graphics driver could not compile the model viewer. Use wireframe mode.');}objects.push(['Shader',s]);return s;};
  const program=gl.createProgram();objects.push(['Program',program]);
  gl.attachShader(program,shader(gl.VERTEX_SHADER,'attribute vec3 p;attribute vec2 uv;attribute vec4 color;uniform vec3 center;uniform float radius;uniform vec2 turn;uniform vec2 fit;varying vec2 tex;varying vec4 shade;void main(){vec3 v=(p-center)/radius;float x=v.x*cos(turn.x)+v.z*sin(turn.x);float z=-v.x*sin(turn.x)+v.z*cos(turn.x);float y=v.y*cos(turn.y)-z*sin(turn.y);z=v.y*sin(turn.y)+z*cos(turn.y);gl_Position=vec4(x*fit.x,y*fit.y,z*.45,1.);tex=uv;shade=color;}'));
  gl.attachShader(program,shader(gl.FRAGMENT_SHADER,'precision mediump float;varying vec2 tex;varying vec4 shade;uniform sampler2D image;uniform float textured;uniform float wire;uniform vec4 line;void main(){vec4 c=mix(shade*mix(vec4(1.),texture2D(image,fract(tex)),textured),line,wire);if(c.a<.1)discard;gl_FragColor=c;}'));
  gl.linkProgram(program);if(!gl.getProgramParameter(program,gl.LINK_STATUS))return null;
  gl.useProgram(program);gl.enable(gl.DEPTH_TEST);gl.disable(gl.CULL_FACE);
  const locations=Object.fromEntries(['center','radius','turn','fit','image','textured','wire','line'].map(n=>[n,gl.getUniformLocation(program,n)]));
  const low=[Infinity,Infinity,Infinity],high=[-Infinity,-Infinity,-Infinity];for(const v of model.vertices)for(let k=0;k<3;k++){low[k]=Math.min(low[k],v[k]);high[k]=Math.max(high[k],v[k]);}
  const center=low.map((v,k)=>(v+high[k])/2);let radius=1e-12;for(const v of model.vertices)radius=Math.max(radius,Math.hypot(...v.map((x,k)=>x-center[k])));
  gl.uniform3fv(locations.center,center);gl.uniform1f(locations.radius,radius);gl.uniform1i(locations.image,0);
  const grouped=new Map();for(const face of model.surfaces){if(!grouped.has(face.material))grouped.set(face.material,[]);const vertices=grouped.get(face.material);face.indices.forEach((idx,i)=>vertices.push(...model.vertices[idx],...face.uv[i],...face.colors[i].map(v=>v/255)));}
  const batches=[];for(const [material,vertices] of grouped){const buffer=gl.createBuffer();objects.push(['Buffer',buffer]);gl.bindBuffer(gl.ARRAY_BUFFER,buffer);gl.bufferData(gl.ARRAY_BUFFER,new Float32Array(vertices),gl.STATIC_DRAW);batches.push({material,buffer,count:vertices.length/9});}
  const edges=[];for(const edge of model.edges)for(const idx of edge)edges.push(...model.vertices[idx],0,0,1,1,1,1);const lineBuffer=gl.createBuffer();objects.push(['Buffer',lineBuffer]);gl.bindBuffer(gl.ARRAY_BUFFER,lineBuffer);gl.bufferData(gl.ARRAY_BUFFER,new Float32Array(edges),gl.STATIC_DRAW);
  let disposed=false;
  for(const material of model.materials||[]){
    const texture=gl.createTexture();textures.push(texture);gl.bindTexture(gl.TEXTURE_2D,texture);gl.texImage2D(gl.TEXTURE_2D,0,gl.RGBA,1,1,0,gl.RGBA,gl.UNSIGNED_BYTE,new Uint8Array([255,255,255,255]));
    gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MIN_FILTER,gl.NEAREST);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MAG_FILTER,gl.NEAREST);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_S,gl.CLAMP_TO_EDGE);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_T,gl.CLAMP_TO_EDGE);
    if(/^data:image\/png;base64,/.test(material.image||'')){const img=new Image();images.push(img);img.onload=()=>{if(disposed||!active())return;gl.bindTexture(gl.TEXTURE_2D,texture);gl.texImage2D(gl.TEXTURE_2D,0,gl.RGBA,gl.RGBA,gl.UNSIGNED_BYTE,img);refresh();};img.onerror=()=>{if(active())canvas.dispatchEvent(new CustomEvent('modeltextureerror'));};img.src=material.image;}
  }
  const white=gl.createTexture();textures.push(white);gl.bindTexture(gl.TEXTURE_2D,white);gl.texImage2D(gl.TEXTURE_2D,0,gl.RGBA,1,1,0,gl.RGBA,gl.UNSIGNED_BYTE,new Uint8Array([255,255,255,255]));gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MIN_FILTER,gl.NEAREST);
  const attributes=[['p',3,0],['uv',2,12],['color',4,20]].map(([name,n,offset])=>[gl.getAttribLocation(program,name),n,offset]);
  return {draw(yaw,pitch,zoom,width,height,mode){
    if(disposed)return;gl.viewport(0,0,canvas.width,canvas.height);const style=getComputedStyle(document.documentElement),color=style.getPropertyValue('--bg').trim();const pixel=document.createElement('canvas').getContext('2d');pixel.fillStyle=color;pixel.fillRect(0,0,1,1);const rgba=pixel.getImageData(0,0,1,1).data;gl.clearColor(rgba[0]/255,rgba[1]/255,rgba[2]/255,1);gl.clear(gl.COLOR_BUFFER_BIT|gl.DEPTH_BUFFER_BIT);
    gl.useProgram(program);pixel.fillStyle=style.getPropertyValue('--color-status-info-fill').trim();pixel.fillRect(0,0,1,1);const line=pixel.getImageData(0,0,1,1).data;gl.uniform4f(locations.line,line[0]/255,line[1]/255,line[2]/255,1);gl.uniform1f(locations.wire,mode==='wireframe'?1:0);
    gl.uniform2f(locations.turn,yaw,pitch);gl.uniform2f(locations.fit,.84*zoom*Math.min(width,height)/width,.84*zoom*Math.min(width,height)/height);
    for(const batch of mode==='wireframe'?[{buffer:lineBuffer,count:edges.length/9,material:-1}]:batches){gl.bindBuffer(gl.ARRAY_BUFFER,batch.buffer);for(const [location,n,offset] of attributes){gl.enableVertexAttribArray(location);gl.vertexAttribPointer(location,n,gl.FLOAT,false,36,offset);}gl.bindTexture(gl.TEXTURE_2D,textures[batch.material]||white);gl.uniform1f(locations.textured,mode==='textured'&&model.materials?.[batch.material]?.image?1:0);gl.drawArrays(mode==='wireframe'?gl.LINES:gl.TRIANGLES,0,batch.count);}
  },dispose(){if(disposed)return;disposed=true;for(const img of images){img.onload=img.onerror=null;img.src='';}for(const [type,object] of objects)gl['delete'+type](object);for(const texture of textures)gl.deleteTexture(texture);gl.getExtension('WEBGL_lose_context')?.loseContext();}};
}
