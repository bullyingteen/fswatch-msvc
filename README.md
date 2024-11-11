# fswatch 

## DESCRIPTION

This is a pretty simple C++20 module that exports namespace fswatch with:
+ enum class EventType
+ struct Event
+ struct Configuration
+ class Service

## USAGE

Service basically spawns a background thread that polls filesystem for updates.

```cpp
int main() {
  std::vector<std::filesystem::path> paths{"watched/directory", "watched/sub/directory"};
  fswatch::Configuration conf{ .latency = std::chrono::milliseconds{100}, .recursive = false };
  fswatch::Service svc{paths, conf};
  try {
    svc.start();
  
    while (svc.is_running()) {
      if (svc.wait_events_for(std::chrono::minutes{1})) {
        for (auto ev : svc.pop_events()) {
           std::cout << ev.to_string() << std::endl; 
        }
        svc.request_stop();
      }
    }
  
    svc.rethrow();
    return 0;
  } catch (const std::exception& e) {
    std::cerr << e.what() << std::endl;
    return -1;  
  } 
}

```

## MOTIVATION

I personally wanted libfswatch for my project on windows but it is really annoying to build with cygwin (In fact I could not make it work in 2 hours despite installed everything).
Thus this fork basically removes everything but Windows API.
Tested on MSVC.
